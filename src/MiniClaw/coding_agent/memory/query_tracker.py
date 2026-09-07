from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
_QUERY_FILLER = {
    "a",
    "an",
    "are",
    "did",
    "do",
    "does",
    "for",
    "in",
    "is",
    "of",
    "the",
    "to",
    "was",
    "what",
    "when",
    "where",
    "which",
    "who",
    "whom",
}


def normalize_query(query: str) -> str:
    """Normalize superficial spelling differences without changing query meaning."""

    normalized = unicodedata.normalize("NFKC", query).casefold()
    return " ".join(_NON_WORD.sub(" ", normalized).split())


def query_similarity(left: str, right: str) -> float:
    """Return a conservative lexical similarity for detecting repeated search attempts."""

    left_normalized = normalize_query(left)
    right_normalized = normalize_query(right)
    if not left_normalized or not right_normalized:
        return 0.0
    if left_normalized == right_normalized:
        return 1.0
    left_tokens = {token for token in left_normalized.split() if token not in _QUERY_FILLER}
    right_tokens = {token for token in right_normalized.split() if token not in _QUERY_FILLER}
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    sequence = SequenceMatcher(None, left_normalized, right_normalized).ratio()
    return max(jaccard, sequence)


@dataclass(slots=True)
class SearchAttempt:
    scope: str
    query: str
    normalized_query: str
    limit: int
    evidence_ids: frozenset[str]
    payload: list[dict[str, Any]]


@dataclass(slots=True, frozen=True)
class SearchProgress:
    status: str
    similarity: float = 0.0
    compared_query: str = ""
    new_evidence_ids: tuple[str, ...] = ()

    @property
    def no_progress(self) -> bool:
        return self.status in {"cache_hit", "no_progress"}

    def metadata(self) -> dict[str, Any]:
        message = {
            "cache_hit": "Repeated query; reused the previous result without running retrieval again.",
            "no_progress": (
                "This near-duplicate query returned no new evidence. Change the entity, relation, "
                "time constraint, or other search terms before searching again."
            ),
            "new_evidence": "The related query produced additional evidence.",
            "new_query": "New search query.",
        }[self.status]
        return {
            "status": self.status,
            "noProgress": self.no_progress,
            "similarity": round(self.similarity, 6),
            "comparedQuery": self.compared_query,
            "newEvidenceIds": list(self.new_evidence_ids),
            "message": message,
        }


class QueryTracker:
    """Track search progress inside one agent run; no state is persisted across runs."""

    def __init__(self, *, similarity_threshold: float = 0.86, history_limit: int = 64) -> None:
        self.similarity_threshold = similarity_threshold
        self.history_limit = history_limit
        self._attempts: list[SearchAttempt] = []

    def cached(self, scope: str, query: str, limit: int) -> SearchAttempt | None:
        normalized = normalize_query(query)
        for attempt in reversed(self._attempts):
            if (
                attempt.scope == scope
                and attempt.normalized_query == normalized
                and attempt.limit >= limit
            ):
                return SearchAttempt(
                    scope=attempt.scope,
                    query=attempt.query,
                    normalized_query=attempt.normalized_query,
                    limit=limit,
                    evidence_ids=attempt.evidence_ids,
                    payload=copy.deepcopy(attempt.payload[:limit]),
                )
        return None

    def cache_progress(self, attempt: SearchAttempt) -> SearchProgress:
        return SearchProgress(
            status="cache_hit",
            similarity=1.0,
            compared_query=attempt.query,
        )

    def observe(
        self,
        scope: str,
        query: str,
        limit: int,
        evidence_ids: set[str],
        payload: list[dict[str, Any]],
    ) -> SearchProgress:
        closest: SearchAttempt | None = None
        closest_similarity = 0.0
        for attempt in reversed(self._attempts):
            if attempt.scope != scope:
                continue
            similarity = query_similarity(query, attempt.query)
            if similarity > closest_similarity:
                closest = attempt
                closest_similarity = similarity

        new_evidence = (
            tuple(sorted(evidence_ids - set(closest.evidence_ids))) if closest is not None else ()
        )
        if closest is None or closest_similarity < self.similarity_threshold:
            progress = SearchProgress(status="new_query")
        elif new_evidence:
            progress = SearchProgress(
                status="new_evidence",
                similarity=closest_similarity,
                compared_query=closest.query,
                new_evidence_ids=new_evidence,
            )
        else:
            progress = SearchProgress(
                status="no_progress",
                similarity=closest_similarity,
                compared_query=closest.query,
            )

        self._attempts.append(
            SearchAttempt(
                scope=scope,
                query=query,
                normalized_query=normalize_query(query),
                limit=limit,
                evidence_ids=frozenset(evidence_ids),
                payload=copy.deepcopy(payload),
            )
        )
        if len(self._attempts) > self.history_limit:
            del self._attempts[: len(self._attempts) - self.history_limit]
        return progress


def attach_search_metadata(
    payload: list[dict[str, Any]], progress: SearchProgress
) -> list[dict[str, Any]]:
    rendered = copy.deepcopy(payload)
    if progress.no_progress:
        rendered.append({"_searchMeta": progress.metadata()})
    return rendered
