from __future__ import annotations

import hashlib
import inspect
import math
import os
import re
import sqlite3
import threading
import time
from array import array
from collections.abc import Mapping, Sequence
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any


_WORD_RE = re.compile(r"[A-Za-z0-9_.-]+|[\u4e00-\u9fff]+")
_ENGLISH_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "had",
    "has",
    "have",
    "he",
    "her",
    "his",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "she",
    "that",
    "the",
    "their",
    "these",
    "they",
    "this",
    "to",
    "was",
    "were",
    "with",
    "you",
}
_PHRASE_STOPWORDS = _ENGLISH_STOPWORDS | {
    "can",
    "could",
    "did",
    "do",
    "does",
    "good",
    "i",
    "idea",
    "me",
    "my",
    "our",
    "please",
    "should",
    "think",
    "we",
    "will",
    "would",
}
_PERSISTENT_CACHE_TARGET = 100_000
_PERSISTENT_CACHE_PRUNE_AT = 110_000


@dataclass(slots=True, frozen=True)
class RetrievalConfig:
    bm25_weight: float = 0.68
    vector_weight: float = 0.22
    exact_weight: float = 0.36
    rrf_k: int = 60
    candidate_pool: int = 24
    vector_dimensions: int = 384
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    cross_encoder_weight: float = 0.70
    cross_encoder_candidate_pool: int = 48
    ann_enabled: bool = True
    ann_hnsw_m: int = 32
    ann_ef_search: int = 96


@dataclass(slots=True, frozen=True)
class MemoryDocument:
    record_id: str
    category: str
    content: str
    subject: str = ""
    relation: str = ""
    value: str = ""
    version: int | None = None
    created_at: str = ""
    status: str = ""
    confidence: float = 1.0
    source_kind: str = ""

    @property
    def conflict_key(self) -> str:
        if not self.subject.strip() or not self.relation.strip() or self.version is None:
            return ""
        subject = " ".join(self.subject.casefold().split())
        relation = " ".join(self.relation.casefold().split())
        return f"{subject}\x1f{relation}"


@dataclass(slots=True, frozen=True)
class RetrievalHit:
    document: MemoryDocument
    score: float
    rrf_score: float
    bm25_score: float
    vector_score: float
    bm25_rank: int | None
    vector_rank: int | None
    exact_score: float = 0.0
    exact_rank: int | None = None
    deterministic_score: float = 0.0
    cross_encoder_score: float | None = None
    superseded_record_ids: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class RerankResult:
    score: float
    deterministic_score: float
    cross_encoder_score: float | None


def search_memory_documents(
    retriever: Any,
    query: str,
    documents: list[MemoryDocument],
    limit: int,
    *,
    use_cross_encoder: bool = True,
) -> list[RetrievalHit]:
    """Call a retriever without breaking older injected implementations.

    MiniClaw's built-in retriever accepts ``use_cross_encoder``. Tests and
    downstream integrations may inject a smaller retriever that implements the
    original three-argument protocol. Inspecting the callable avoids masking a
    genuine ``TypeError`` raised inside the custom implementation.
    """

    search = retriever.search
    try:
        parameters = inspect.signature(search).parameters.values()
        supports_option = any(
            parameter.name == "use_cross_encoder"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    except (TypeError, ValueError):
        supports_option = False
    if supports_option:
        return search(
            query,
            documents,
            limit,
            use_cross_encoder=use_cross_encoder,
        )
    return search(query, documents, limit)


def rerank_memory_documents(
    retriever: Any,
    query: str,
    documents: list[MemoryDocument],
    rank_scores: list[float],
    *,
    use_cross_encoder: bool = True,
) -> tuple[list[RerankResult], dict[str, Any]]:
    """Run the final rerank without breaking recall-only retrievers.

    Older injected retrievers may expose only ``search``. They still receive
    the deterministic content rerank, while Cross-Encoder is reported as
    unavailable instead of failing the whole memory request.
    """

    rerank = getattr(retriever, "rerank_documents", None)
    if callable(rerank):
        try:
            parameters = inspect.signature(rerank).parameters.values()
            supports_option = any(
                parameter.name == "use_cross_encoder"
                or parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            supports_option = False
        if supports_option:
            results = rerank(
                query,
                documents,
                rank_scores,
                use_cross_encoder=use_cross_encoder,
            )
        else:
            results = rerank(query, documents, rank_scores)
        return results, dict(getattr(retriever, "last_diagnostics", {}))

    max_rank_score = max(rank_scores, default=1.0)
    deterministic = [
        deterministic_rerank_score(query, document, rank_score, max_rank_score)
        for document, rank_score in zip(documents, rank_scores, strict=True)
    ]
    return (
        [RerankResult(score, score, None) for score in deterministic],
        {
            "cross_encoder_enabled": False,
            "cross_encoder_applied": False,
            "cross_encoder_candidates": 0,
            "cross_encoder_seconds": 0.0,
            "cross_encoder_load_error": "Injected retriever has no rerank_documents method",
        },
    )


def deterministic_rerank_score(
    query: str,
    document: MemoryDocument,
    rank_score: float,
    max_rank_score: float,
) -> float:
    """Rerank one candidate after any rank-based fusion stage."""

    query_tokens = set(_bm25_tokens(query))
    query_normalized = " ".join(query.casefold().split())
    doc_tokens = set(_bm25_tokens(f"{document.subject} {document.content}"))
    coverage = len(query_tokens & doc_tokens) / max(1, len(query_tokens))
    phrase = 1.0 if query_normalized in document.content.casefold() else 0.0
    subject_overlap = len(query_tokens & set(_bm25_tokens(document.subject))) / max(
        1, len(query_tokens)
    )
    category_boost = 1.0 if document.category == "preference" else 0.0
    exact_content_phrase = 1.0 if _has_exact_content_phrase(query, document.content) else 0.0
    score = (
        0.70 * (rank_score / max(max_rank_score, 1e-12))
        + 0.18 * coverage
        + 0.07 * subject_overlap
        + 0.04 * phrase
        + 0.01 * category_boost
        + 0.13 * exact_content_phrase
    )
    status_weight = {
        "completed": 1.0,
        "resolved": 1.0,
        "active": 0.82,
        "verifying": 0.78,
        "waiting_for_user": 0.72,
        "failed": 0.55,
        "cancelled": 0.45,
        "inactive": 0.25,
    }.get(document.status.casefold(), 0.9)
    score *= 0.88 + 0.12 * status_weight
    score *= 0.92 + 0.08 * max(0.0, min(1.0, document.confidence))
    if _is_temporal_query(query):
        score += 0.08 * _freshness_score(document.created_at)
    return score


def _is_temporal_query(query: str) -> bool:
    return bool(
        re.search(
            r"\b(?:current(?:ly)?|latest|newest|recent|now|today|still)\b|"
            r"(?:目前|当前|现在|最新|最近|今天|仍然)",
            query,
            re.IGNORECASE,
        )
    )


def _freshness_score(value: str) -> float:
    if not value.strip():
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86_400)
    except ValueError:
        return 0.0
    return math.exp(-age_days / 90.0)


def tokenize(text: str) -> list[str]:
    normalized = " ".join(text.casefold().split())
    tokens: list[str] = []
    for match in _WORD_RE.finditer(normalized):
        value = match.group(0)
        if value and "\u4e00" <= value[0] <= "\u9fff":
            tokens.extend(value)
            tokens.extend(value[index : index + 2] for index in range(len(value) - 1))
        else:
            tokens.append(value)
    return [token for token in tokens if token]


@lru_cache(maxsize=50_000)
def _bm25_tokens(text: str) -> tuple[str, ...]:
    """Tokenize lexical retrieval text with lightweight English normalization."""

    values: list[str] = []
    for token in tokenize(text):
        if not token.isascii():
            values.append(token)
            continue
        if token in _ENGLISH_STOPWORDS:
            continue
        values.append(token)
        if len(token) > 4 and token.endswith("ed"):
            base = token[:-2]
            values.append(base)
            values.append(base + "e")
        elif len(token) > 5 and token.endswith("ing"):
            base = token[:-3]
            values.append(base)
            values.append(base + "e")
        elif len(token) > 5 and token.endswith("ness"):
            base = token[:-4]
            values.append(base)
            if base.endswith("i"):
                values.append(base[:-1] + "y")
        elif len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            values.append(token[:-1])
    return tuple(values)


def _has_exact_content_phrase(query: str, content: str) -> bool:
    """Return whether content preserves a meaningful multi-token query phrase.

    Dense similarity and unigram BM25 can confuse isolated words with a
    multiword topic. Exact adjacent content-word phrases provide a small,
    domain-independent rerank signal.
    """

    query_tokens = tokenize(query)
    runs: list[list[str]] = []
    current: list[str] = []
    for token in query_tokens:
        if token.isascii() and token in _PHRASE_STOPWORDS:
            if current:
                runs.append(current)
                current = []
            continue
        current.append(token)
    if current:
        runs.append(current)
    query_counts = Counter(query_tokens)
    phrases = {
        " ".join(run[index : index + size])
        for run in runs
        for size in range(2, min(4, len(run)) + 1)
        for index in range(len(run) - size + 1)
        if all(query_counts[token] == 1 for token in run[index : index + size])
    }
    if not phrases:
        return False
    normalized_content = f" {' '.join(tokenize(content))} "
    return any(f" {phrase} " in normalized_content for phrase in phrases)


_WINDOWS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/])(?:[^\s<>:\"|?*]+[\\/])*[^\s<>:\"|?*]+"
)
_SLASH_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:\.{0,2}/|/)?(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
)
_FILE_NAME_RE = re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_-]+\.)+[A-Za-z0-9_-]+")
_ERROR_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:ERR_[A-Z0-9_]+|E[A-Z0-9_]{2,}|[A-Z]{2,}[\-_]\d{2,}|HTTP\s*[45]\d\d|[45]\d\d)(?![A-Za-z0-9_])"
)
_QUALIFIED_SYMBOL_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_]*\.)+[A-Za-z_][A-Za-z0-9_]*(?![A-Za-z0-9_])"
)
_IDENTIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Z][A-Za-z0-9]+(?:[A-Z][A-Za-z0-9]+)+|[a-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*|[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+)(?![A-Za-z0-9_])"
)
def exact_search_symbols(text: str) -> tuple[str, ...]:
    """Extract code/file/error identifiers for the exact retrieval channel.

    This deliberately recognizes identifier *shapes*, not benchmark phrases.
    Natural-language words remain the responsibility of BM25 and dense recall.
    """

    values: list[str] = []
    occupied: list[tuple[int, int]] = []
    patterns = (
        _WINDOWS_PATH_RE,
        _SLASH_PATH_RE,
        _ERROR_CODE_RE,
        _QUALIFIED_SYMBOL_RE,
        _FILE_NAME_RE,
        _IDENTIFIER_RE,
    )
    for pattern in patterns:
        for match in pattern.finditer(text):
            if any(match.start() < end and match.end() > start for start, end in occupied):
                continue
            raw = match.group(0).strip("`'\"()[]{}<>,;:")
            if not raw:
                continue
            if "\\" in raw or "/" in raw:
                normalized = raw.replace("\\", "/").casefold()
            else:
                normalized = raw.casefold()
            values.append(normalized)
            occupied.append((match.start(), match.end()))
    return tuple(dict.fromkeys(values))


def _exact_symbol_scores(query: str, documents: list[MemoryDocument]) -> list[float]:
    query_symbols = set(exact_search_symbols(query))
    if not query_symbols:
        return [0.0] * len(documents)
    scores: list[float] = []
    for document in documents:
        document_symbols = set(
            exact_search_symbols(f"{document.subject}\n{document.content}")
        )
        matches = query_symbols & document_symbols
        if not matches:
            scores.append(0.0)
            continue
        coverage = len(matches) / len(query_symbols)
        specificity = sum(min(2.0, 0.5 + len(symbol) / 24.0) for symbol in matches)
        scores.append(coverage + specificity / max(1, len(query_symbols)))
    return scores


class LocalHashVectorEncoder:
    """Dependency-free dense vectors using signed feature hashing.

    Token and character n-gram features make this usable for Chinese and source-code terms.
    The interface can later be replaced by a remote embedding provider without changing fusion.
    """

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions < 32:
            raise ValueError("vector dimensions must be at least 32")
        self.dimensions = dimensions
        self._cache: dict[str, array[float]] = {}

    def encode(self, text: str) -> Sequence[float]:
        normalized = " ".join(text.casefold().split())
        cached = self._cache.get(normalized)
        if cached is not None:
            return cached
        features = tokenize(normalized)
        compact = re.sub(r"\s+", "", normalized)
        features.extend(f"#3:{compact[index:index + 3]}" for index in range(max(0, len(compact) - 2)))
        counts = Counter(features)
        vector = [0.0] * self.dimensions
        for feature, count in counts.items():
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimensions
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(value * value for value in vector))
        encoded = array("f", (value / norm for value in vector)) if norm else array("f", vector)
        if len(self._cache) < 50_000:
            self._cache[normalized] = encoded
        return encoded

    def encode_many(self, texts: list[str]) -> list[Sequence[float]]:
        return [self.encode(text) for text in texts]


class PersistentRetrievalCache:
    """SQLite-backed lexical index and embedding cache.

    The retriever still accepts an explicit document list on every call, which
    keeps source filtering and conflict resolution deterministic. This cache
    removes repeated document tokenization, inverted-index construction, and
    local model encoding across both queries and process restarts.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ann_directory = self.path.with_suffix(self.path.suffix + ".ann")
        self._lock = threading.RLock()
        self._ann_indices: dict[str, Any] = {}
        self.last_ann_diagnostics: dict[str, Any] = {}
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        # DELETE journaling avoids lingering mapped WAL handles on Windows,
        # which otherwise prevents temporary workspaces from being removed.
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS retrieval_documents (
                    document_key TEXT PRIMARY KEY,
                    token_count INTEGER NOT NULL,
                    token_frequencies TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS retrieval_postings (
                    token TEXT NOT NULL,
                    document_key TEXT NOT NULL,
                    term_frequency INTEGER NOT NULL,
                    PRIMARY KEY (token, document_key)
                );
                CREATE INDEX IF NOT EXISTS retrieval_postings_token
                    ON retrieval_postings(token);
                CREATE TABLE IF NOT EXISTS retrieval_embeddings (
                    encoder_key TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (encoder_key, text_hash)
                );
                CREATE TABLE IF NOT EXISTS retrieval_ann_meta (
                    index_key TEXT PRIMARY KEY,
                    encoder_key TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    hnsw_m INTEGER NOT NULL,
                    next_label INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS retrieval_ann_items (
                    index_key TEXT NOT NULL,
                    label INTEGER NOT NULL,
                    document_key TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (index_key, label),
                    UNIQUE (index_key, document_key)
                );
                CREATE INDEX IF NOT EXISTS retrieval_ann_items_document
                    ON retrieval_ann_items(index_key, document_key, active);
                """
            )

    @staticmethod
    def document_key(document: MemoryDocument) -> str:
        payload = "\0".join(
            (
                document.record_id,
                document.category,
                document.subject,
                document.relation,
                document.value,
                str(document.version),
                document.created_at,
                document.status,
                str(document.confidence),
                document.source_kind,
                document.content,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def lexical_state(
        self,
        documents: list[MemoryDocument],
        query_tokens: list[str],
    ) -> tuple[list[str], list[int], list[dict[str, int]], Counter[str]]:
        keys = [self.document_key(document) for document in documents]
        existing: dict[str, int] = {}
        with self._lock, self._connection() as connection:
            for start in range(0, len(keys), 800):
                batch = keys[start : start + 800]
                if not batch:
                    continue
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    "SELECT document_key, token_count "
                    f"FROM retrieval_documents WHERE document_key IN ({placeholders})",
                    batch,
                ).fetchall()
                for document_key, token_count in rows:
                    existing[str(document_key)] = int(token_count)

            now = time.time()
            if existing:
                connection.executemany(
                    "UPDATE retrieval_documents SET updated_at = ? WHERE document_key = ?",
                    ((now, key) for key in existing),
                )
            indexed_new = 0
            for key, document in zip(keys, documents, strict=True):
                if key in existing:
                    continue
                frequencies = Counter(
                    _bm25_tokens(f"{document.subject} {document.content}")
                )
                token_count = sum(frequencies.values())
                connection.execute(
                    "INSERT OR REPLACE INTO retrieval_documents "
                    "(document_key, token_count, token_frequencies, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        key,
                        token_count,
                        "{}",
                        now,
                    ),
                )
                connection.executemany(
                    "INSERT OR REPLACE INTO retrieval_postings "
                    "(token, document_key, term_frequency) VALUES (?, ?, ?)",
                    ((token, key, count) for token, count in frequencies.items()),
                )
                existing[key] = token_count
                indexed_new += 1

            if indexed_new:
                self._prune_documents(connection, protected_keys=set(keys))

            indices_by_key: dict[str, list[int]] = {}
            for index, key in enumerate(keys):
                indices_by_key.setdefault(key, []).append(index)
            frequencies: list[dict[str, int]] = [{} for _ in documents]
            document_frequency: Counter[str] = Counter()
            for token in query_tokens:
                rows = connection.execute(
                    "SELECT document_key, term_frequency FROM retrieval_postings WHERE token = ?",
                    (token,),
                ).fetchall()
                for document_key, term_frequency in rows:
                    indices = indices_by_key.get(str(document_key), [])
                    for index in indices:
                        frequencies[index][token] = int(term_frequency)
                    document_frequency[token] += len(indices)

        lengths = [existing[key] for key in keys]
        return keys, lengths, frequencies, document_frequency

    def cached_vectors(
        self,
        encoder_key: str,
        texts: list[str],
    ) -> tuple[list[Sequence[float] | None], list[int]]:
        hashes = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts]
        cached: dict[str, Sequence[float]] = {}
        with self._lock, self._connection() as connection:
            for start in range(0, len(hashes), 800):
                batch = hashes[start : start + 800]
                if not batch:
                    continue
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    "SELECT text_hash, dimensions, vector FROM retrieval_embeddings "
                    f"WHERE encoder_key = ? AND text_hash IN ({placeholders})",
                    [encoder_key, *batch],
                ).fetchall()
                for text_hash, dimensions, blob in rows:
                    vector = array("f")
                    vector.frombytes(blob)
                    if len(vector) == int(dimensions):
                        cached[str(text_hash)] = vector
                if rows:
                    connection.executemany(
                        "UPDATE retrieval_embeddings SET updated_at = ? "
                        "WHERE encoder_key = ? AND text_hash = ?",
                        ((time.time(), encoder_key, str(row[0])) for row in rows),
                    )
        values = [cached.get(text_hash) for text_hash in hashes]
        return values, [index for index, value in enumerate(values) if value is None]

    def store_vectors(
        self,
        encoder_key: str,
        texts: list[str],
        vectors: list[Sequence[float]],
    ) -> None:
        now = time.time()
        rows = []
        for text, vector in zip(texts, vectors, strict=True):
            encoded = array("f", (float(value) for value in vector))
            rows.append(
                (
                    encoder_key,
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    len(encoded),
                    encoded.tobytes(),
                    now,
                )
            )
        with self._lock, self._connection() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO retrieval_embeddings "
                "(encoder_key, text_hash, dimensions, vector, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            self._prune_embeddings(connection)

    def ann_scope_is_indexed(
        self,
        encoder_key: str,
        document_keys: list[str],
        *,
        hnsw_m: int,
    ) -> bool:
        """Return whether one compatible ANN index already contains the full scope."""

        if not document_keys:
            return True
        unique_keys = set(document_keys)
        with self._lock, self._connection() as connection:
            index_rows = connection.execute(
                "SELECT index_key FROM retrieval_ann_meta "
                "WHERE encoder_key = ? AND hnsw_m = ?",
                (encoder_key, hnsw_m),
            ).fetchall()
            for (index_key,) in index_rows:
                present: set[str] = set()
                keys = list(unique_keys)
                for start in range(0, len(keys), 800):
                    batch = keys[start : start + 800]
                    placeholders = ",".join("?" for _ in batch)
                    rows = connection.execute(
                        "SELECT document_key FROM retrieval_ann_items "
                        f"WHERE index_key = ? AND active = 1 AND document_key IN ({placeholders})",
                        [str(index_key), *batch],
                    ).fetchall()
                    present.update(str(row[0]) for row in rows)
                if present == unique_keys:
                    return True
        return False

    def ann_search(
        self,
        encoder_key: str,
        document_keys: list[str],
        document_vectors: list[Sequence[float]] | None,
        query_vector: Sequence[float],
        *,
        limit: int,
        hnsw_m: int,
        ef_search: int,
    ) -> list[float] | None:
        """Search a persistent FAISS HNSW index and filter to the active scope.

        The on-disk index may contain documents from several memory scopes, but
        results are always filtered against ``document_keys`` supplied by the
        caller. Search depth grows until enough allowed hits are found, so an
        item from another user/channel/session can never leak into the result.
        """

        if not document_keys:
            return []
        started = time.perf_counter()
        dimensions = len(query_vector)
        if dimensions <= 0 or (
            document_vectors is not None
            and any(len(vector) != dimensions for vector in document_vectors)
        ):
            self.last_ann_diagnostics = {
                "ann_backend": "linear-fallback",
                "ann_applied": False,
                "ann_error": "inconsistent vector dimensions",
                "ann_seconds": time.perf_counter() - started,
            }
            return None
        try:
            import faiss
            import numpy as np
        except Exception as exc:  # Optional dependency.
            self.last_ann_diagnostics = {
                "ann_backend": "linear-fallback",
                "ann_applied": False,
                "ann_error": f"{type(exc).__name__}: {exc}",
                "ann_seconds": time.perf_counter() - started,
            }
            return None

        index_key = hashlib.sha256(
            f"faiss-hnsw-v1\0{encoder_key}\0{dimensions}\0{hnsw_m}".encode("utf-8")
        ).hexdigest()
        try:
            with self._lock:
                index = self._load_or_rebuild_ann_index(
                    faiss,
                    np,
                    index_key=index_key,
                    encoder_key=encoder_key,
                    dimensions=dimensions,
                    hnsw_m=hnsw_m,
                )
                if document_vectors is None:
                    labels_by_key: dict[str, int] = {}
                    with self._connection() as connection:
                        for start in range(0, len(document_keys), 800):
                            batch = document_keys[start : start + 800]
                            placeholders = ",".join("?" for _ in batch)
                            rows = connection.execute(
                                "SELECT document_key, label FROM retrieval_ann_items "
                                f"WHERE index_key = ? AND active = 1 AND document_key IN ({placeholders})",
                                [index_key, *batch],
                            ).fetchall()
                            labels_by_key.update(
                                (str(key), int(label)) for key, label in rows
                            )
                    if any(key not in labels_by_key for key in document_keys):
                        self.last_ann_diagnostics = {
                            "ann_backend": "faiss-hnsw-cache-miss",
                            "ann_applied": False,
                            "ann_error": "document vectors are not fully indexed",
                            "ann_scope_size": len(labels_by_key),
                            "ann_seconds": time.perf_counter() - started,
                        }
                        return None
                else:
                    labels_by_key = self._sync_ann_documents(
                        faiss,
                        np,
                        index,
                        index_key=index_key,
                        encoder_key=encoder_key,
                        dimensions=dimensions,
                        hnsw_m=hnsw_m,
                        document_keys=document_keys,
                        document_vectors=document_vectors,
                    )
                faiss.downcast_index(index.index).hnsw.efSearch = max(ef_search, limit)
                query = np.asarray([query_vector], dtype="float32")
                faiss.normalize_L2(query)
                allowed_labels = {
                    labels_by_key[key] for key in document_keys if key in labels_by_key
                }
                wanted = max(1, min(limit, len(allowed_labels)))
                search_k = min(index.ntotal, max(64, wanted * 4))
                matched: dict[int, float] = {}
                while search_k > 0:
                    distances, labels = index.search(query, search_k)
                    for score, label in zip(distances[0], labels[0], strict=True):
                        numeric_label = int(label)
                        if numeric_label in allowed_labels and numeric_label not in matched:
                            matched[numeric_label] = max(0.0, float(score))
                    if len(matched) >= wanted or search_k >= index.ntotal:
                        break
                    search_k = min(index.ntotal, max(search_k + 1, search_k * 2))
                scores = [
                    matched.get(labels_by_key.get(key, -1), 0.0) for key in document_keys
                ]
                self.last_ann_diagnostics = {
                    "ann_backend": "faiss-hnsw",
                    "ann_applied": True,
                    "ann_error": "",
                    "ann_dimensions": dimensions,
                    "ann_index_size": int(index.ntotal),
                    "ann_scope_size": len(allowed_labels),
                    "ann_search_k": search_k,
                    "ann_vector_source": (
                        "persistent-index" if document_vectors is None else "synchronized-documents"
                    ),
                    "ann_seconds": time.perf_counter() - started,
                }
                return scores
        except Exception as exc:
            self.last_ann_diagnostics = {
                "ann_backend": "linear-fallback",
                "ann_applied": False,
                "ann_error": f"{type(exc).__name__}: {exc}",
                "ann_seconds": time.perf_counter() - started,
            }
            return None

    def _ann_index_path(self, index_key: str) -> Path:
        return self.ann_directory / f"{index_key}.faiss"

    @staticmethod
    def _new_ann_index(faiss: Any, dimensions: int, hnsw_m: int) -> Any:
        base = faiss.IndexHNSWFlat(dimensions, hnsw_m, faiss.METRIC_INNER_PRODUCT)
        return faiss.IndexIDMap2(base)

    def _load_or_rebuild_ann_index(
        self,
        faiss: Any,
        np: Any,
        *,
        index_key: str,
        encoder_key: str,
        dimensions: int,
        hnsw_m: int,
    ) -> Any:
        cached = self._ann_indices.get(index_key)
        if cached is not None:
            with self._connection() as connection:
                active_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM retrieval_ann_items "
                        "WHERE index_key = ? AND active = 1",
                        (index_key,),
                    ).fetchone()[0]
                )
            if cached.ntotal == active_count:
                return cached
            self._ann_indices.pop(index_key, None)
        self.ann_directory.mkdir(parents=True, exist_ok=True)
        path = self._ann_index_path(index_key)
        with self._connection() as connection:
            meta = connection.execute(
                "SELECT encoder_key, dimensions, hnsw_m FROM retrieval_ann_meta "
                "WHERE index_key = ?",
                (index_key,),
            ).fetchone()
            item_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM retrieval_ann_items "
                    "WHERE index_key = ? AND active = 1",
                    (index_key,),
                ).fetchone()[0]
            )
        index = None
        if (
            meta is not None
            and str(meta[0]) == encoder_key
            and int(meta[1]) == dimensions
            and int(meta[2]) == hnsw_m
            and path.exists()
        ):
            try:
                loaded = faiss.read_index(str(path))
                if loaded.d == dimensions and loaded.ntotal == item_count:
                    index = loaded
            except Exception:
                index = None
        if index is None:
            index = self._new_ann_index(faiss, dimensions, hnsw_m)
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT label, vector FROM retrieval_ann_items "
                    "WHERE index_key = ? AND active = 1 ORDER BY label",
                    (index_key,),
                ).fetchall()
                if rows:
                    vectors = np.asarray(
                        [self._decode_vector(blob, dimensions) for _, blob in rows],
                        dtype="float32",
                    )
                    labels = np.asarray([int(label) for label, _ in rows], dtype="int64")
                    faiss.normalize_L2(vectors)
                    index.add_with_ids(vectors, labels)
                connection.execute(
                    "INSERT OR REPLACE INTO retrieval_ann_meta "
                    "(index_key, encoder_key, dimensions, hnsw_m, next_label, updated_at) "
                    "VALUES (?, ?, ?, ?, COALESCE((SELECT MAX(label) + 1 FROM "
                    "retrieval_ann_items WHERE index_key = ?), 0), ?)",
                    (index_key, encoder_key, dimensions, hnsw_m, index_key, time.time()),
                )
            self._write_ann_index(faiss, index, path)
        self._ann_indices[index_key] = index
        return index

    def _sync_ann_documents(
        self,
        faiss: Any,
        np: Any,
        index: Any,
        *,
        index_key: str,
        encoder_key: str,
        dimensions: int,
        hnsw_m: int,
        document_keys: list[str],
        document_vectors: list[Sequence[float]],
    ) -> dict[str, int]:
        unique_vectors: dict[str, Sequence[float]] = {}
        for key, vector in zip(document_keys, document_vectors, strict=True):
            unique_vectors.setdefault(key, vector)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT document_key, label, active FROM retrieval_ann_items "
                "WHERE index_key = ?",
                (index_key,),
            ).fetchall()
            all_labels_by_key = {str(key): int(label) for key, label, _ in rows}
            labels_by_key = {
                key: all_labels_by_key[key]
                for key in unique_vectors
                if key in all_labels_by_key
            }
            inactive_keys = {
                str(key) for key, _, active in rows if not int(active) and str(key) in unique_vectors
            }
            if inactive_keys:
                connection.executemany(
                    "UPDATE retrieval_ann_items SET active = 1, updated_at = ? "
                    "WHERE index_key = ? AND document_key = ?",
                    ((time.time(), index_key, key) for key in inactive_keys),
                )
            present_labels = {
                int(label) for label in faiss.vector_to_array(index.id_map)
            }
            missing_from_index = [
                label for label in labels_by_key.values() if label not in present_labels
            ]
            if missing_from_index:
                placeholders = ",".join("?" for _ in missing_from_index)
                stored_rows = connection.execute(
                    "SELECT label, vector FROM retrieval_ann_items "
                    f"WHERE index_key = ? AND active = 1 AND label IN ({placeholders})",
                    [index_key, *missing_from_index],
                ).fetchall()
                stored_vectors = np.asarray(
                    [self._decode_vector(blob, dimensions) for _, blob in stored_rows],
                    dtype="float32",
                )
                stored_labels = np.asarray(
                    [int(label) for label, _ in stored_rows], dtype="int64"
                )
                faiss.normalize_L2(stored_vectors)
                index.add_with_ids(stored_vectors, stored_labels)
            missing = [key for key in unique_vectors if key not in all_labels_by_key]
            if not missing:
                if missing_from_index or inactive_keys:
                    self._write_ann_index(
                        faiss, index, self._ann_index_path(index_key)
                    )
                return labels_by_key
            meta = connection.execute(
                "SELECT next_label FROM retrieval_ann_meta WHERE index_key = ?",
                (index_key,),
            ).fetchone()
            next_label = int(meta[0]) if meta is not None else 0
            labels = list(range(next_label, next_label + len(missing)))
            now = time.time()
            connection.executemany(
                "INSERT INTO retrieval_ann_items "
                "(index_key, label, document_key, dimensions, vector, active, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (
                    (
                        index_key,
                        label,
                        key,
                        dimensions,
                        array("f", (float(value) for value in unique_vectors[key])).tobytes(),
                        now,
                    )
                    for key, label in zip(missing, labels, strict=True)
                ),
            )
            connection.execute(
                "INSERT OR REPLACE INTO retrieval_ann_meta "
                "(index_key, encoder_key, dimensions, hnsw_m, next_label, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    index_key,
                    encoder_key,
                    dimensions,
                    hnsw_m,
                    next_label + len(missing),
                    now,
                ),
            )
        vectors = np.asarray([unique_vectors[key] for key in missing], dtype="float32")
        numeric_labels = np.asarray(labels, dtype="int64")
        faiss.normalize_L2(vectors)
        index.add_with_ids(vectors, numeric_labels)
        self._write_ann_index(faiss, index, self._ann_index_path(index_key))
        labels_by_key.update(zip(missing, labels, strict=True))
        return labels_by_key

    @staticmethod
    def _decode_vector(blob: bytes, dimensions: int) -> list[float]:
        vector = array("f")
        vector.frombytes(blob)
        if len(vector) != dimensions:
            raise ValueError("stored ANN vector dimensions do not match index metadata")
        return list(vector)

    @staticmethod
    def _write_ann_index(faiss: Any, index: Any, path: Path) -> None:
        temporary = path.with_name(
            f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        faiss.write_index(index, str(temporary))
        os.replace(temporary, path)

    @staticmethod
    def _prune_documents(
        connection: sqlite3.Connection,
        *,
        protected_keys: set[str] | None = None,
    ) -> None:
        count = int(connection.execute("SELECT COUNT(*) FROM retrieval_documents").fetchone()[0])
        if count <= _PERSISTENT_CACHE_PRUNE_AT:
            return
        protected = protected_keys or set()
        excess = max(0, count - _PERSISTENT_CACHE_TARGET)
        rows = connection.execute(
            "SELECT document_key FROM retrieval_documents ORDER BY updated_at ASC"
        ).fetchall()
        removable = [str(row[0]) for row in rows if str(row[0]) not in protected][:excess]
        for start in range(0, len(removable), 800):
            batch = removable[start : start + 800]
            placeholders = ",".join("?" for _ in batch)
            connection.execute(
                f"DELETE FROM retrieval_documents WHERE document_key IN ({placeholders})",
                batch,
            )
        connection.execute(
            "DELETE FROM retrieval_postings WHERE document_key NOT IN ("
            "SELECT document_key FROM retrieval_documents)"
        )
        connection.execute(
            "UPDATE retrieval_ann_items SET active = 0 WHERE document_key NOT IN ("
            "SELECT document_key FROM retrieval_documents)"
        )

    @staticmethod
    def _prune_embeddings(connection: sqlite3.Connection) -> None:
        count = int(connection.execute("SELECT COUNT(*) FROM retrieval_embeddings").fetchone()[0])
        if count <= _PERSISTENT_CACHE_PRUNE_AT:
            return
        connection.execute(
            "DELETE FROM retrieval_embeddings WHERE rowid NOT IN ("
            "SELECT rowid FROM retrieval_embeddings ORDER BY updated_at DESC LIMIT ?)",
            (_PERSISTENT_CACHE_TARGET,),
        )


def _local_huggingface_model_path(model_name: str) -> Path | None:
    explicit = Path(model_name)
    if explicit.exists():
        return explicit.resolve()
    cache_root = Path(
        os.getenv("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
    ) / "hub"
    model_root = cache_root / f"models--{model_name.replace('/', '--')}"
    snapshots = model_root / "snapshots"
    if not snapshots.exists():
        return None
    values = sorted(
        (
            path
            for path in snapshots.iterdir()
            if path.is_dir() and _complete_huggingface_snapshot(path)
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return values[0] if values else None


def _complete_huggingface_snapshot(path: Path) -> bool:
    if not (path / "config.json").is_file():
        return False
    weight_names = (
        "model.safetensors",
        "model.safetensors.index.json",
        "pytorch_model.bin",
        "pytorch_model.bin.index.json",
    )
    return any((path / name).is_file() for name in weight_names)


class SentenceTransformerVectorEncoder:
    """Optional cached local semantic vectors with a dependency-free fallback."""

    _MODELS: dict[str, Any] = {}
    _MODEL_LOCK = threading.RLock()

    def __init__(
        self,
        model_name: str | None = None,
        fallback: LocalHashVectorEncoder | None = None,
        *,
        batch_size: int = 8,
        cache_size: int = 50_000,
        device: str = "auto",
        query_prefix: str = "",
        document_prefix: str = "",
    ) -> None:
        self.model_name = model_name or os.getenv(
            "MINICLAW_MEMORY_VECTOR_MODEL",
            "BAAI/bge-m3",
        )
        self.fallback = fallback or LocalHashVectorEncoder()
        self.batch_size = max(1, batch_size)
        self.cache_size = max(1, cache_size)
        self.device = self._resolve_device(device)
        self.query_prefix = query_prefix
        self.document_prefix = document_prefix
        self._cache: dict[str, Sequence[float]] = {}
        self.load_error = ""
        self.last_backend = "uninitialized"
        self.last_seconds = 0.0

    def _model(self) -> Any | None:
        if self.load_error:
            return None
        with self._MODEL_LOCK:
            cache_key = f"{self.model_name}\0{self.device}"
            if cache_key in self._MODELS:
                return self._MODELS[cache_key]
            try:
                from sentence_transformers import SentenceTransformer

                local_path = self._local_model_path()
                if local_path is None:
                    self.load_error = (
                        f"Local sentence-transformer cache not found: {self.model_name}"
                    )
                    return None
                model = SentenceTransformer(str(local_path), device=self.device)
            except Exception as exc:  # Optional runtime dependency/cache.
                self.load_error = f"{type(exc).__name__}: {exc}"
                return None
            self._MODELS[cache_key] = model
            return model

    @staticmethod
    def _resolve_device(device: str) -> str:
        normalized = device.strip().casefold()
        if normalized != "auto":
            if not normalized:
                raise ValueError("vector device must not be empty")
            return normalized
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def _local_model_path(self) -> Path | None:
        return _local_huggingface_model_path(self.model_name)

    def encode(self, text: str) -> Sequence[float]:
        return self.encode_many([text])[0]

    def prepare_query(self, text: str) -> str:
        return f"{self.query_prefix}{text}" if self.query_prefix else text

    def prepare_document(self, text: str) -> str:
        return f"{self.document_prefix}{text}" if self.document_prefix else text

    def encode_many(self, texts: list[str]) -> list[Sequence[float]]:
        started = time.perf_counter()
        missing = list(dict.fromkeys(text for text in texts if text not in self._cache))
        model = self._model()
        if missing and model is not None:
            with self._MODEL_LOCK:
                values = model.encode(
                    missing,
                    batch_size=self.batch_size,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                )
            for text, vector in zip(missing, values, strict=True):
                if len(self._cache) >= self.cache_size:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[text] = [float(value) for value in vector]
            self.last_backend = "sentence-transformer"
        elif missing:
            for text, vector in zip(missing, self.fallback.encode_many(missing), strict=True):
                if len(self._cache) >= self.cache_size:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[text] = vector
            self.last_backend = "local-hash-fallback"
        elif model is not None:
            self.last_backend = "sentence-transformer-cache"
        else:
            self.last_backend = "local-hash-fallback-cache"
        self.last_seconds = time.perf_counter() - started
        return [self._cache[text] for text in texts]


class CrossEncoderReranker:
    """Lazy, local-only Cross-Encoder scoring for a small RRF candidate set."""

    _MODELS: dict[str, Any] = {}
    _MODEL_LOCK = threading.RLock()

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        *,
        batch_size: int = 4,
        max_length: int = 512,
        cache_size: int = 20_000,
        device: str = "auto",
    ) -> None:
        self.model_name = model_name
        self.batch_size = max(1, batch_size)
        self.max_length = max(64, max_length)
        self.cache_size = max(1, cache_size)
        self.device = SentenceTransformerVectorEncoder._resolve_device(device)
        self.load_error = ""
        self._score_cache: dict[tuple[str, str], float] = {}

    def _model(self) -> Any | None:
        if self.load_error:
            return None
        with self._MODEL_LOCK:
            cache_key = f"{self.model_name}\0{self.device}\0{self.max_length}"
            if cache_key in self._MODELS:
                return self._MODELS[cache_key]
            try:
                import torch
                from sentence_transformers import CrossEncoder

                local_path = _local_huggingface_model_path(self.model_name)
                if local_path is None:
                    self.load_error = f"Local Cross-Encoder cache not found: {self.model_name}"
                    return None
                model = CrossEncoder(
                    str(local_path),
                    device=self.device,
                    max_length=self.max_length,
                    default_activation_function=torch.nn.Identity(),
                )
            except Exception as exc:  # Optional runtime dependency/cache.
                self.load_error = f"{type(exc).__name__}: {exc}"
                return None
            self._MODELS[cache_key] = model
            return model

    def score(self, query: str, documents: list[MemoryDocument]) -> list[float] | None:
        if not documents:
            return []
        keys = [
            (
                query,
                f"{document.subject.strip()[:400]}\n{document.content}".strip(),
            )
            for document in documents
        ]
        missing = list(dict.fromkeys(key for key in keys if key not in self._score_cache))
        model = self._model()
        if missing and model is None:
            return None
        if missing:
            pairs = [[query_text, document_text] for query_text, document_text in missing]
            with self._MODEL_LOCK:
                values = model.predict(
                    pairs,
                    batch_size=self.batch_size,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                )
            for key, value in zip(missing, values, strict=True):
                if len(self._score_cache) >= self.cache_size:
                    self._score_cache.pop(next(iter(self._score_cache)))
                self._score_cache[key] = float(value)
        return [self._score_cache[key] for key in keys]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=False))


class HybridMemoryRetriever:
    """Exact symbols + persistent BM25 + dense ANN, RRF, then Cross-Encoder."""

    def __init__(
        self,
        config: RetrievalConfig | None = None,
        vector_encoder: Any | None = None,
        reranker: CrossEncoderReranker | Any | None = None,
        cache_path: Path | None = None,
    ) -> None:
        self.config = config or RetrievalConfig()
        total_weight = self.config.bm25_weight + self.config.vector_weight
        if total_weight <= 0 or self.config.bm25_weight <= self.config.vector_weight:
            raise ValueError("BM25 weight must be positive and greater than vector weight")
        if self.config.exact_weight < 0:
            raise ValueError("exact search weight must not be negative")
        if not 4 <= self.config.ann_hnsw_m <= 128:
            raise ValueError("ANN HNSW M must be between 4 and 128")
        if not 8 <= self.config.ann_ef_search <= 2_048:
            raise ValueError("ANN efSearch must be between 8 and 2048")
        if not 0.0 <= self.config.cross_encoder_weight <= 1.0:
            raise ValueError("Cross-Encoder weight must be between zero and one")
        if not 1 <= self.config.cross_encoder_candidate_pool <= 50:
            raise ValueError("Cross-Encoder candidate pool must be between 1 and 50")
        self.vector_encoder = vector_encoder or LocalHashVectorEncoder(
            self.config.vector_dimensions
        )
        self.reranker = reranker
        self.cache = PersistentRetrievalCache(cache_path) if cache_path is not None else None
        self.last_diagnostics: dict[str, Any] = {}

    def search(
        self,
        query: str,
        documents: list[MemoryDocument],
        limit: int = 5,
        *,
        use_cross_encoder: bool = True,
    ) -> list[RetrievalHit]:
        if not query.strip() or not documents:
            self.last_diagnostics = {
                **self._vector_diagnostics(),
                "exact_symbols": [],
                "exact_candidates": 0,
                "cross_encoder_enabled": bool(self.reranker),
                "cross_encoder_applied": False,
                "cross_encoder_candidates": 0,
                "cross_encoder_seconds": 0.0,
            }
            return []
        documents, superseded = self._active_documents(documents)
        bm25_scores = self._bm25(query, documents)
        vector_scores = self._vector(query, documents)
        exact_scores = _exact_symbol_scores(query, documents)
        vector_diagnostics = {
            **self._vector_diagnostics(),
            "exact_symbols": list(exact_search_symbols(query)),
            "exact_candidates": sum(score > 0 for score in exact_scores),
        }
        bm25_order = self._rank(bm25_scores)
        vector_order = self._rank(vector_scores)
        exact_order = self._rank(exact_scores)
        bm25_ranks = {index: rank for rank, index in enumerate(bm25_order, start=1)}
        vector_ranks = {index: rank for rank, index in enumerate(vector_order, start=1)}
        exact_ranks = {index: rank for rank, index in enumerate(exact_order, start=1)}
        candidates = set(bm25_order[: self.config.candidate_pool])
        candidates.update(vector_order[: self.config.candidate_pool])
        candidates.update(exact_order[: self.config.candidate_pool])
        fused: list[tuple[int, float]] = []
        for index in candidates:
            score = 0.0
            if index in bm25_ranks:
                score += self.config.bm25_weight / (self.config.rrf_k + bm25_ranks[index])
            if index in vector_ranks:
                score += self.config.vector_weight / (self.config.rrf_k + vector_ranks[index])
            if index in exact_ranks:
                score += self.config.exact_weight / (self.config.rrf_k + exact_ranks[index])
            fused.append((index, score))
        fused.sort(key=lambda item: item[1], reverse=True)
        fused_documents = [documents[index] for index, _ in fused]
        rerank_results = self.rerank_documents(
            query,
            fused_documents,
            [rrf_score for _, rrf_score in fused],
            use_cross_encoder=use_cross_encoder,
        )
        self.last_diagnostics = {**vector_diagnostics, **self.last_diagnostics}
        hits: list[RetrievalHit] = []
        for (index, rrf_score), reranked in zip(fused, rerank_results, strict=True):
            document = documents[index]
            hits.append(
                RetrievalHit(
                    document=document,
                    score=reranked.score,
                    rrf_score=rrf_score,
                    bm25_score=bm25_scores[index],
                    vector_score=vector_scores[index],
                    bm25_rank=bm25_ranks.get(index),
                    vector_rank=vector_ranks.get(index),
                    exact_score=exact_scores[index],
                    exact_rank=exact_ranks.get(index),
                    deterministic_score=reranked.deterministic_score,
                    cross_encoder_score=reranked.cross_encoder_score,
                    superseded_record_ids=superseded.get(document.record_id, ()),
                )
            )
        hits.sort(
            key=lambda hit: (
                hit.score,
                -(hit.bm25_rank or 10**9),
                hit.bm25_score,
            ),
            reverse=True,
        )
        return hits[: max(1, min(limit, 50))]

    def rerank_documents(
        self,
        query: str,
        documents: list[MemoryDocument],
        rank_scores: list[float],
        *,
        use_cross_encoder: bool = True,
    ) -> list[RerankResult]:
        if len(documents) != len(rank_scores):
            raise ValueError("documents and rank_scores must have the same length")
        if not documents:
            self.last_diagnostics = {
                "cross_encoder_enabled": bool(self.reranker),
                "cross_encoder_applied": False,
                "cross_encoder_candidates": 0,
                "cross_encoder_seconds": 0.0,
            }
            return []
        max_rank_score = max(rank_scores, default=1.0)
        deterministic = [
            deterministic_rerank_score(query, document, rank_score, max_rank_score)
            for document, rank_score in zip(documents, rank_scores, strict=True)
        ]
        diagnostics = {
            "cross_encoder_enabled": bool(self.reranker),
            "cross_encoder_applied": False,
            "cross_encoder_candidates": 0,
            "cross_encoder_seconds": 0.0,
        }
        if not use_cross_encoder or self.reranker is None:
            self.last_diagnostics = diagnostics
            return [RerankResult(score, score, None) for score in deterministic]

        order = sorted(range(len(documents)), key=lambda index: deterministic[index], reverse=True)
        selected = order[: self.config.cross_encoder_candidate_pool]
        started = time.perf_counter()
        raw_scores = self.reranker.score(query, [documents[index] for index in selected])
        diagnostics.update(
            {
                "cross_encoder_model": str(getattr(self.reranker, "model_name", "custom")),
                "cross_encoder_candidates": len(selected),
                "cross_encoder_seconds": time.perf_counter() - started,
                "cross_encoder_load_error": str(getattr(self.reranker, "load_error", "")),
            }
        )
        if raw_scores is None:
            self.last_diagnostics = diagnostics
            return [RerankResult(score, score, None) for score in deterministic]

        normalized_deterministic = [
            score / max(max(deterministic), 1e-12) for score in deterministic
        ]
        cross_scores: list[float | None] = [None] * len(documents)
        for index, raw_score in zip(selected, raw_scores, strict=True):
            bounded = max(-60.0, min(60.0, float(raw_score)))
            cross_scores[index] = 1.0 / (1.0 + math.exp(-bounded))
        weight = self.config.cross_encoder_weight
        results = []
        for index, deterministic_score in enumerate(deterministic):
            cross_score = cross_scores[index]
            score = normalized_deterministic[index]
            if cross_score is not None:
                score = (1.0 - weight) * score + weight * cross_score
            results.append(RerankResult(score, deterministic_score, cross_score))
        diagnostics["cross_encoder_applied"] = True
        self.last_diagnostics = diagnostics
        return results

    @staticmethod
    def _active_documents(
        documents: list[MemoryDocument],
    ) -> tuple[list[MemoryDocument], dict[str, tuple[str, ...]]]:
        """Resolve versioned conflicts before relevance ranking.

        Retrieval score answers "is this relevant?"; it must not decide which
        contradictory version is active. Structured documents that share a
        subject/relation conflict key are collapsed to the greatest version.
        Unstructured documents keep their existing behavior.
        """

        grouped: dict[str, list[tuple[int, MemoryDocument]]] = {}
        active_with_index: list[tuple[int, MemoryDocument]] = []
        for index, document in enumerate(documents):
            key = document.conflict_key
            if not key:
                active_with_index.append((index, document))
                continue
            grouped.setdefault(key, []).append((index, document))

        superseded: dict[str, tuple[str, ...]] = {}
        for members in grouped.values():
            latest_index, latest = max(
                members,
                key=lambda item: (item[1].version if item[1].version is not None else -1, item[0]),
            )
            active_with_index.append((latest_index, latest))
            older = tuple(
                document.record_id
                for _, document in sorted(
                    members,
                    key=lambda item: (
                        item[1].version if item[1].version is not None else -1,
                        item[0],
                    ),
                    reverse=True,
                )
                if document.record_id != latest.record_id
            )
            if older:
                superseded[latest.record_id] = older

        active_with_index.sort(key=lambda item: item[0])
        return [document for _, document in active_with_index], superseded

    def _bm25(self, query: str, documents: list[MemoryDocument]) -> list[float]:
        # Long prompts and multiple-choice options often repeat the same entity
        # many times. Cap query term frequency at one so repeated common words
        # cannot drown out rare, decisive terms.
        query_tokens = list(dict.fromkeys(_bm25_tokens(query)))
        if self.cache is not None:
            _, document_lengths, token_frequencies, document_frequency = (
                self.cache.lexical_state(
                    documents,
                    query_tokens,
                )
            )
        else:
            doc_tokens = [
                _bm25_tokens(f"{document.subject} {document.content}")
                for document in documents
            ]
            document_lengths = [len(tokens) for tokens in doc_tokens]
            token_frequencies = [dict(Counter(tokens)) for tokens in doc_tokens]
            document_frequency = Counter(
                token
                for frequencies in token_frequencies
                for token in frequencies
            )
        average_length = sum(document_lengths) / max(1, len(document_lengths))
        scores: list[float] = []
        for length, frequencies in zip(
            document_lengths,
            token_frequencies,
            strict=True,
        ):
            score = 0.0
            for token in query_tokens:
                frequency = frequencies.get(token, 0)
                if not frequency:
                    continue
                containing = document_frequency[token]
                idf = math.log(1.0 + (len(documents) - containing + 0.5) / (containing + 0.5))
                denominator = frequency + self.config.bm25_k1 * (
                    1.0
                    - self.config.bm25_b
                    + self.config.bm25_b * length / max(1.0, average_length)
                )
                score += idf * frequency * (self.config.bm25_k1 + 1.0) / denominator
            scores.append(score)
        return scores

    def _vector(self, query: str, documents: list[MemoryDocument]) -> list[float]:
        document_texts = [
            f"{document.subject.strip()}\n{document.content}".strip()
            for document in documents
        ]
        prepare_query = getattr(self.vector_encoder, "prepare_query", None)
        prepare_document = getattr(self.vector_encoder, "prepare_document", None)
        prepared_query = prepare_query(query) if callable(prepare_query) else query
        prepared_documents = [
            prepare_document(text) if callable(prepare_document) else text
            for text in document_texts
        ]
        texts = [prepared_query, *prepared_documents]
        encode_many = getattr(self.vector_encoder, "encode_many", None)
        if self.cache is None:
            vectors = encode_many(texts) if callable(encode_many) else [
                self.vector_encoder.encode(text) for text in texts
            ]
        else:
            document_keys = [self.cache.document_key(document) for document in documents]
            encoder_key = self._vector_encoder_key()
            indexed_scope = self.config.ann_enabled and self.cache.ann_scope_is_indexed(
                encoder_key,
                document_keys,
                hnsw_m=self.config.ann_hnsw_m,
            )
            if indexed_scope:
                encoder_key, query_vectors = self._cached_vectors_for_texts(
                    [prepared_query], encode_many
                )
                query_vector = query_vectors[0]
                cached_ann_scores = self.cache.ann_search(
                    encoder_key,
                    document_keys,
                    None,
                    query_vector,
                    limit=self.config.candidate_pool,
                    hnsw_m=self.config.ann_hnsw_m,
                    ef_search=self.config.ann_ef_search,
                )
                if cached_ann_scores is not None:
                    return cached_ann_scores
            encoder_key, vectors = self._cached_vectors_for_texts(texts, encode_many)
        query_vector = vectors[0]
        document_vectors = vectors[1:]
        if self.cache is not None and self.config.ann_enabled:
            ann_scores = self.cache.ann_search(
                encoder_key,
                [self.cache.document_key(document) for document in documents],
                document_vectors,
                query_vector,
                limit=self.config.candidate_pool,
                hnsw_m=self.config.ann_hnsw_m,
                ef_search=self.config.ann_ef_search,
            )
            if ann_scores is not None:
                return ann_scores
        return [
            max(0.0, cosine_similarity(query_vector, vector))
            for vector in document_vectors
        ]

    def _cached_vectors_for_texts(
        self,
        texts: list[str],
        encode_many: Any,
    ) -> tuple[str, list[Sequence[float]]]:
        if self.cache is None:
            raise RuntimeError("persistent vector cache is unavailable")
        encoder_key = self._vector_encoder_key()
        cached, missing_indices = self.cache.cached_vectors(encoder_key, texts)
        if missing_indices:
            missing_texts = [texts[index] for index in missing_indices]
            missing_vectors = (
                encode_many(missing_texts)
                if callable(encode_many)
                else [self.vector_encoder.encode(text) for text in missing_texts]
            )
            resolved_encoder_key = self._vector_encoder_key()
            if resolved_encoder_key != encoder_key:
                encoder_key = resolved_encoder_key
                cached, missing_indices = self.cache.cached_vectors(encoder_key, texts)
                if missing_indices:
                    missing_texts = [texts[index] for index in missing_indices]
                    missing_vectors = (
                        encode_many(missing_texts)
                        if callable(encode_many)
                        else [self.vector_encoder.encode(text) for text in missing_texts]
                    )
            self.cache.store_vectors(encoder_key, missing_texts, list(missing_vectors))
            for index, vector in zip(missing_indices, missing_vectors, strict=True):
                cached[index] = vector
        if any(vector is None for vector in cached):
            raise RuntimeError("vector cache did not resolve every requested text")
        return encoder_key, [vector for vector in cached if vector is not None]

    def _vector_encoder_key(self) -> str:
        encoder = self.vector_encoder
        if isinstance(encoder, SentenceTransformerVectorEncoder):
            model_path = encoder._local_model_path()
            backend = (
                "sentence-transformer"
                if model_path is not None
                and not encoder.load_error
                and "fallback" not in encoder.last_backend
                else "local-hash-fallback"
            )
            return (
                f"{backend}:{encoder.model_name}:{encoder.device}:"
                f"q={encoder.query_prefix}:d={encoder.document_prefix}"
            )
        dimensions = getattr(encoder, "dimensions", self.config.vector_dimensions)
        return f"{type(encoder).__module__}.{type(encoder).__qualname__}:{dimensions}"

    def _vector_diagnostics(self) -> dict[str, Any]:
        encoder = self.vector_encoder
        backend = str(
            getattr(
                encoder,
                "last_backend",
                "local-hash" if isinstance(encoder, LocalHashVectorEncoder) else "custom",
            )
        )
        load_error = str(getattr(encoder, "load_error", ""))
        diagnostics = {
            "vector_backend": backend,
            "vector_model": str(getattr(encoder, "model_name", "local-hash")),
            "vector_device": str(getattr(encoder, "device", "cpu")),
            "vector_load_error": load_error,
            "vector_fallback": "fallback" in backend or bool(load_error),
            "vector_seconds": float(getattr(encoder, "last_seconds", 0.0)),
        }
        if self.cache is not None:
            diagnostics.update(self.cache.last_ann_diagnostics)
        else:
            diagnostics.update(
                {
                    "ann_backend": "disabled-no-persistent-cache",
                    "ann_applied": False,
                    "ann_error": "",
                    "ann_seconds": 0.0,
                }
            )
        return diagnostics

    @staticmethod
    def _rank(scores: list[float]) -> list[int]:
        return [
            index
            for index, score in sorted(
                enumerate(scores), key=lambda item: (item[1], -item[0]), reverse=True
            )
            if score > 0
        ]


def _environment_boolean(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _environment_integer(
    env: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = env.get(name)
    try:
        value = default if raw is None or not raw.strip() else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _environment_float(
    env: Mapping[str, str],
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = env.get(name)
    try:
        value = default if raw is None or not raw.strip() else float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def create_hybrid_memory_retriever(
    environment: Mapping[str, str] | None = None,
    *,
    vector_encoder: Any | None = None,
    cache_path: Path | None = None,
) -> HybridMemoryRetriever:
    """Build the production retriever from environment settings.

    Cross-Encoder inference is local-only. If the optional dependency or local
    model cache is unavailable, the retriever records the load error and keeps
    the deterministic BM25/vector/RRF ranking instead of downloading at runtime.
    """

    env = os.environ if environment is None else environment
    vector_enabled = _environment_boolean(env, "MINICLAW_MEMORY_VECTOR_ENABLED", False)
    vector_model_name = env.get(
        "MINICLAW_MEMORY_VECTOR_MODEL",
        "BAAI/bge-m3",
    ).strip()
    if vector_enabled and not vector_model_name:
        raise ValueError("MINICLAW_MEMORY_VECTOR_MODEL must not be empty")
    vector_batch_size = _environment_integer(
        env,
        "MINICLAW_MEMORY_VECTOR_BATCH_SIZE",
        8,
        1,
        512,
    )
    vector_device = env.get("MINICLAW_MEMORY_VECTOR_DEVICE", "auto").strip()
    if vector_enabled and not vector_device:
        raise ValueError("MINICLAW_MEMORY_VECTOR_DEVICE must not be empty")
    query_prefix = env.get("MINICLAW_MEMORY_VECTOR_QUERY_PREFIX", "")
    document_prefix = env.get("MINICLAW_MEMORY_VECTOR_DOCUMENT_PREFIX", "")
    resolved_vector_encoder = vector_encoder
    if resolved_vector_encoder is None and vector_enabled:
        resolved_vector_encoder = SentenceTransformerVectorEncoder(
            vector_model_name,
            batch_size=vector_batch_size,
            device=vector_device,
            query_prefix=query_prefix,
            document_prefix=document_prefix,
        )

    enabled = _environment_boolean(env, "MINICLAW_MEMORY_CROSS_ENCODER_ENABLED", False)
    model_name = env.get(
        "MINICLAW_MEMORY_CROSS_ENCODER_MODEL",
        "BAAI/bge-reranker-v2-m3",
    ).strip()
    if enabled and not model_name:
        raise ValueError("MINICLAW_MEMORY_CROSS_ENCODER_MODEL must not be empty")
    candidate_pool = _environment_integer(
        env,
        "MINICLAW_MEMORY_CROSS_ENCODER_CANDIDATES",
        48,
        1,
        50,
    )
    weight = _environment_float(
        env,
        "MINICLAW_MEMORY_CROSS_ENCODER_WEIGHT",
        0.70,
        0.0,
        1.0,
    )
    batch_size = _environment_integer(
        env,
        "MINICLAW_MEMORY_CROSS_ENCODER_BATCH_SIZE",
        4,
        1,
        128,
    )
    max_length = _environment_integer(
        env,
        "MINICLAW_MEMORY_CROSS_ENCODER_MAX_LENGTH",
        512,
        64,
        2_048,
    )
    reranker_device = env.get("MINICLAW_MEMORY_CROSS_ENCODER_DEVICE", "auto").strip()
    if enabled and not reranker_device:
        raise ValueError("MINICLAW_MEMORY_CROSS_ENCODER_DEVICE must not be empty")
    exact_weight = _environment_float(
        env,
        "MINICLAW_MEMORY_EXACT_WEIGHT",
        0.36,
        0.0,
        2.0,
    )
    ann_enabled = _environment_boolean(env, "MINICLAW_MEMORY_ANN_ENABLED", True)
    ann_hnsw_m = _environment_integer(
        env,
        "MINICLAW_MEMORY_ANN_HNSW_M",
        32,
        4,
        128,
    )
    ann_ef_search = _environment_integer(
        env,
        "MINICLAW_MEMORY_ANN_EF_SEARCH",
        96,
        8,
        2_048,
    )
    reranker = (
        CrossEncoderReranker(
            model_name,
            batch_size=batch_size,
            max_length=max_length,
            device=reranker_device,
        )
        if enabled
        else None
    )
    return HybridMemoryRetriever(
        RetrievalConfig(
            exact_weight=exact_weight,
            cross_encoder_weight=weight,
            cross_encoder_candidate_pool=candidate_pool,
            ann_enabled=ann_enabled,
            ann_hnsw_m=ann_hnsw_m,
            ann_ef_search=ann_ef_search,
        ),
        vector_encoder=resolved_vector_encoder,
        reranker=reranker,
        cache_path=cache_path,
    )
