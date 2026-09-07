from __future__ import annotations

from MiniClaw.agent.context import is_context_update

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from MiniClaw.llm.types import ChatMessage

from .evidence import MemoryEvidenceStore
from .locking import MemoryFileLock
from .retrieval import (
    HybridMemoryRetriever,
    MemoryDocument,
    search_memory_documents,
)
from .semantic import MemoryConflictError, SemanticMemoryStore
from .quality import preference_evidence, memory_write_disallowed, historical_state_claim


_ARTIFACT_PATH = re.compile(r"^Full result:\s*(.+?)\s*$", re.MULTILINE)
_RECENCY_MEMORY_QUERY = re.compile(
    r"\b(?:current(?:ly)?|latest|newest|most\s+recent|now|still|today|at\s+present)\b|"
    r"(?:目前|当前|现在|最新|最近|如今|现阶段)",
    re.IGNORECASE,
)
_RECENCY_QUERY_EXPANSION = (
    "latest recent newest current now updated changed switched replaced moved "
    "started stopped finished no longer still just "
    "最新 最近 当前 现在 更新 改为 切换 替换 开始 停止 刚刚 不再"
)
_PERSONALIZATION_MEMORY_QUERY = re.compile(
    r"\b(?:recommend|recommendation|suggest|suggestion|might\s+find\s+interesting|"
    r"for\s+me|my\s+taste|what\s+should\s+i|which\s+.+\s+should\s+i)\b|"
    r"(?:推荐|建议|适合我|我会喜欢|我的偏好)",
    re.IGNORECASE,
)
_PERSONALIZATION_QUERY_EXPANSION = (
    "user interests preferences expertise field research focus likes dislikes "
    "works on repeatedly requested papers articles topics personalized profile "
    "用户 兴趣 偏好 专业 领域 研究方向 喜欢 不喜欢 反复 提到 个性化"
)
_EXPLICIT_MEMORY = re.compile(
    r"^\s*(?:[-*]\s*)?(?:记住|长期记忆|remember|long[- ]term memory)"
    r"(?:\s*\[(preference|project|environment|fact)\])?\s*[:：]\s*(.+?)\s*$",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True, frozen=True)
class ArchiveMemoryRecord:
    record_id: str
    session_id: str
    source_kind: str
    source_path: str
    role: str
    message_index: int
    chunk_index: int
    content: str
    created_at: str
    action_block_id: str = ""
    event_kind: str = "message"


@dataclass(slots=True, frozen=True)
class ArchiveMemoryHit:
    record: ArchiveMemoryRecord
    score: float
    rrf_score: float
    bm25_score: float
    vector_score: float
    bm25_rank: int | None
    vector_rank: int | None
    deterministic_score: float
    cross_encoder_score: float | None = None
    superseded_record_ids: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ArchiveParentHit:
    """A ranked archive parent expanded around its best matching child chunk."""

    parent_id: str
    anchor: ArchiveMemoryRecord
    records: tuple[ArchiveMemoryRecord, ...]
    score: float
    rrf_score: float
    bm25_score: float
    vector_score: float
    bm25_rank: int | None
    vector_rank: int | None
    deterministic_score: float
    cross_encoder_score: float | None = None
    superseded_record_ids: tuple[str, ...] = ()

    @property
    def content(self) -> str:
        return "\n".join(record.content for record in self.records)


class ArchiveMemoryIndex:
    """Persistent hybrid index over compressed transcript and tool artifacts."""

    def __init__(
        self,
        path: Path,
        workspace: Path,
        retriever: HybridMemoryRetriever | None = None,
        chunk_chars: int = 1_600,
    ) -> None:
        self.path = path
        self.workspace = workspace.resolve()
        self.retriever = retriever or HybridMemoryRetriever()
        self.chunk_chars = max(400, chunk_chars)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records_cache_signature: tuple[int, int] | None = None
        self._records_cache: tuple[ArchiveMemoryRecord, ...] = ()

    def add_messages(
        self,
        session_id: str,
        source_path: str,
        messages: list[ChatMessage],
    ) -> int:
        records: list[ArchiveMemoryRecord] = []
        artifact_paths: dict[str, str] = {}
        call_blocks: dict[str, str] = {}
        pending_block = ""
        for message_index, message in enumerate(messages):
            # Memory-tool responses are derived views of this same index.  If
            # they are indexed again, repeated archive_search calls recursively
            # embed prior JSON results and eventually crowd out source evidence.
            if is_context_update(message) or (message.role == "tool" and message.name == "memory"):
                continue
            if message.role == "user":
                pending_block = ""
            if message.role == "assistant" and message.tool_calls:
                if pending_block and message.content.strip():
                    records.extend(
                        self._records_for_text(
                            session_id=session_id,
                            source_kind="message",
                            source_path=source_path,
                            role=message.role,
                            message_index=message_index,
                            text=f"[Decision after tool result]\n{message.content}",
                            action_block_id=pending_block,
                            event_kind="decision",
                        )
                    )
                block_id = self._action_block_id(session_id, source_path, message_index)
                pending_block = block_id
                for call in message.tool_calls:
                    if call.call_id:
                        call_blocks[call.call_id] = block_id
                rendered_calls = json.dumps(
                    [
                        {
                            "call_id": call.call_id,
                            "name": call.name,
                            "arguments": call.arguments,
                        }
                        for call in message.tool_calls
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                call_text = "\n".join(
                    value
                    for value in (
                        message.content.strip(),
                        f"[Assistant tool calls]\n{rendered_calls}",
                    )
                    if value
                )
                records.extend(
                    self._records_for_text(
                        session_id=session_id,
                        source_kind="message",
                        source_path=source_path,
                        role=message.role,
                        message_index=message_index,
                        text=call_text,
                        action_block_id=block_id,
                        event_kind="tool_call",
                    )
                )
                continue
            action_block_id = ""
            event_kind = "message"
            if message.role == "tool":
                action_block_id = call_blocks.get(message.tool_call_id or "", "")
                pending_block = action_block_id or pending_block
                event_kind = "tool_result"
            elif message.role == "assistant" and pending_block:
                action_block_id = pending_block
                event_kind = "decision"
                pending_block = ""
            if not message.content.strip():
                continue
            records.extend(
                self._records_for_text(
                    session_id=session_id,
                    source_kind="message",
                    source_path=source_path,
                    role=message.role,
                    message_index=message_index,
                    text=message.content,
                    action_block_id=action_block_id,
                    event_kind=event_kind,
                )
            )
            for artifact_path in _ARTIFACT_PATH.findall(message.content):
                artifact_paths[artifact_path] = action_block_id
        for artifact_index, (artifact_path, action_block_id) in enumerate(
            sorted(artifact_paths.items())
        ):
            try:
                path = (self.workspace / artifact_path).resolve(strict=True)
                path.relative_to(self.workspace)
                text = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                continue
            records.extend(
                self._records_for_text(
                    session_id=session_id,
                    source_kind="tool_artifact",
                    source_path=artifact_path,
                    role="tool",
                    message_index=len(messages) + artifact_index,
                    text=text,
                    action_block_id=action_block_id,
                    event_kind="tool_artifact",
                )
            )
        return self._append(records)

    def records(self, session_id: str | None = None) -> list[ArchiveMemoryRecord]:
        if not self.path.exists():
            self._records_cache_signature = None
            self._records_cache = ()
            return []
        stat = self.path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature != self._records_cache_signature:
            values: list[ArchiveMemoryRecord] = []
            for raw_line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    payload = json.loads(raw_line)
                    values.append(
                        ArchiveMemoryRecord(
                            record_id=payload["record_id"],
                            session_id=payload["session_id"],
                            source_kind=payload["source_kind"],
                            source_path=payload["source_path"],
                            role=payload["role"],
                            message_index=payload["message_index"],
                            chunk_index=payload["chunk_index"],
                            content=payload["content"],
                            created_at=payload["created_at"],
                            action_block_id=str(payload.get("action_block_id") or ""),
                            event_kind=str(payload.get("event_kind") or "message"),
                        )
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
            self._records_cache = tuple(values)
            self._records_cache_signature = signature
        if session_id is None:
            return list(self._records_cache)
        return [record for record in self._records_cache if record.session_id == session_id]

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        session_id: str | None = None,
        use_cross_encoder: bool = True,
    ) -> list[ArchiveMemoryHit]:
        records = [
            record
            for record in self.records(session_id)
            if record.source_kind in {"message", "tool_artifact"}
        ]
        # Progressive compaction can archive overlapping closed history more
        # than once. Exact duplicates must not consume several Top-K positions
        # and crowd out distinct evidence. Keep the newest copy so its source
        # path points at the latest surviving archive.
        unique_records: dict[str, ArchiveMemoryRecord] = {}
        for record in records:
            content_key = " ".join(record.content.casefold().split())
            if content_key:
                unique_records[content_key] = record
        records = list(unique_records.values())
        if not records:
            return []
        by_id = {record.record_id: record for record in records}
        documents = [
            MemoryDocument(
                record_id=record.record_id,
                category="archive",
                content=record.content,
                subject=(
                    f"{record.source_kind} {record.role} {record.event_kind} "
                    f"{record.source_path}"
                ),
                created_at=record.created_at,
                status="completed",
                source_kind=record.source_kind,
            )
            for record in records
        ]
        expanded_limit = min(50, max(limit, limit * 4))
        hits = search_memory_documents(
            self.retriever,
            query,
            documents,
            expanded_limit,
            use_cross_encoder=use_cross_encoder,
        )
        expanded_queries: list[str] = []
        if _RECENCY_MEMORY_QUERY.search(query):
            # Questions about the current state are often lexically closest to
            # an obsolete sentence that literally says "current".  Search a
            # second time with generic transition language so raw statements
            # such as "switched to", "replaced", or "no longer" can compete.
            # Keep the better score per record so the rule applies uniformly
            # to current-state questions across domains.
            expanded_queries.append(f"{query}\n{_RECENCY_QUERY_EXPANSION}")
        if _PERSONALIZATION_MEMORY_QUERY.search(query):
            # Generic recommendation prompts often omit the user's domain or
            # taste entirely.  A second profile-oriented query recalls prior
            # statements about interests and repeated subject-matter focus.
            expanded_queries.append(f"{query}\n{_PERSONALIZATION_QUERY_EXPANSION}")
        if expanded_queries:
            best_by_record = {hit.document.record_id: hit for hit in hits}
            for expanded_query in expanded_queries:
                for hit in search_memory_documents(
                    self.retriever,
                    expanded_query,
                    documents,
                    expanded_limit,
                    use_cross_encoder=use_cross_encoder,
                ):
                    current = best_by_record.get(hit.document.record_id)
                    if current is None or hit.score > current.score:
                        best_by_record[hit.document.record_id] = hit
            hits = sorted(
                best_by_record.values(),
                key=lambda hit: (
                    hit.score,
                    -(hit.bm25_rank or 10**9),
                    hit.bm25_score,
                ),
                reverse=True,
            )[:expanded_limit]
        return [
            ArchiveMemoryHit(
                record=by_id[hit.document.record_id],
                score=hit.score,
                rrf_score=hit.rrf_score,
                bm25_score=hit.bm25_score,
                vector_score=hit.vector_score,
                bm25_rank=hit.bm25_rank,
                vector_rank=hit.vector_rank,
                deterministic_score=hit.deterministic_score,
                cross_encoder_score=hit.cross_encoder_score,
                superseded_record_ids=hit.superseded_record_ids,
            )
            for hit in hits[:limit]
        ]

    def search_parents(
        self,
        query: str,
        parent_limit: int = 5,
        *,
        neighbor_radius: int = 1,
        session_id: str | None = None,
        use_cross_encoder: bool = True,
    ) -> list[ArchiveParentHit]:
        """Retrieve child anchors, then return ordered context from their parent messages.

        The hybrid retriever continues to rank small chunks for precision.  Only after
        ranking do we group those chunks by their archive parent, select the best anchor
        from each parent, and expand to adjacent chunks.  This preserves local state and
        action sequences without building a second parent-level vector index.
        """

        parent_limit = max(1, min(parent_limit, 5))
        neighbor_radius = max(0, neighbor_radius)
        candidate_limit = min(50, max(40, parent_limit * 10))
        child_hits = self.search(
            query,
            candidate_limit,
            session_id=session_id,
            use_cross_encoder=use_cross_encoder,
        )
        if not child_hits:
            return []

        records_by_parent: dict[tuple[str, str, str, int], list[ArchiveMemoryRecord]] = {}
        for record in self.records(session_id):
            if record.source_kind not in {"message", "tool_artifact"}:
                continue
            key = self._parent_key(record)
            records_by_parent.setdefault(key, []).append(record)
        for records in records_by_parent.values():
            records.sort(
                key=lambda record: (
                    record.message_index,
                    1 if record.source_kind == "tool_artifact" else 0,
                    record.chunk_index,
                )
            )

        selected: list[ArchiveParentHit] = []
        selected_keys: set[tuple[str, str, str, int]] = set()
        for hit in child_hits:
            key = self._parent_key(hit.record)
            if key in selected_keys:
                continue
            siblings = records_by_parent.get(key, [hit.record])
            if hit.record.action_block_id:
                control_records = [
                    record for record in siblings if record.source_kind == "message"
                ]
                evidence_records: list[ArchiveMemoryRecord] = []
                if hit.record.source_kind == "tool_artifact":
                    lower = hit.record.chunk_index - neighbor_radius
                    upper = hit.record.chunk_index + neighbor_radius
                    evidence_records = [
                        record
                        for record in siblings
                        if record.source_kind == "tool_artifact"
                        and record.source_path == hit.record.source_path
                        and lower <= record.chunk_index <= upper
                    ]
                expanded = tuple(
                    sorted(
                        {record.record_id: record for record in [*control_records, *evidence_records]}.values(),
                        key=lambda record: (
                            record.message_index,
                            1 if record.source_kind == "tool_artifact" else 0,
                            record.chunk_index,
                        ),
                    )
                )
            else:
                lower = hit.record.chunk_index - neighbor_radius
                upper = hit.record.chunk_index + neighbor_radius
                expanded = tuple(
                    record for record in siblings if lower <= record.chunk_index <= upper
                )
            if not expanded:
                expanded = (hit.record,)
            parent_digest = hashlib.sha256(
                "\0".join((key[0], key[1], key[2], str(key[3]))).encode("utf-8")
            ).hexdigest()
            selected.append(
                ArchiveParentHit(
                    parent_id=f"archive_parent_{parent_digest[:20]}",
                    anchor=hit.record,
                    records=expanded,
                    score=hit.score,
                    rrf_score=hit.rrf_score,
                    bm25_score=hit.bm25_score,
                    vector_score=hit.vector_score,
                    bm25_rank=hit.bm25_rank,
                    vector_rank=hit.vector_rank,
                    deterministic_score=hit.deterministic_score,
                    cross_encoder_score=hit.cross_encoder_score,
                    superseded_record_ids=hit.superseded_record_ids,
                )
            )
            selected_keys.add(key)
            if len(selected) >= parent_limit:
                break
        return selected

    @staticmethod
    def _parent_key(record: ArchiveMemoryRecord) -> tuple[str, str, str, int]:
        if record.action_block_id:
            return (
                record.session_id,
                "action_block",
                record.action_block_id,
                -1,
            )
        return (
            record.session_id,
            record.source_kind,
            record.source_path,
            record.message_index,
        )

    def _records_for_text(
        self,
        *,
        session_id: str,
        source_kind: str,
        source_path: str,
        role: str,
        message_index: int,
        text: str,
        action_block_id: str = "",
        event_kind: str = "message",
    ) -> list[ArchiveMemoryRecord]:
        records: list[ArchiveMemoryRecord] = []
        for chunk_index, chunk in enumerate(self._chunks(text.strip())):
            records.append(
                self._record(
                    session_id=session_id,
                    source_kind=source_kind,
                    source_path=source_path,
                    role=role,
                    message_index=message_index,
                    chunk_index=chunk_index,
                    content=chunk,
                    action_block_id=action_block_id,
                    event_kind=event_kind,
                )
            )
        return records

    def _record(
        self,
        *,
        session_id: str,
        source_kind: str,
        source_path: str,
        role: str,
        message_index: int,
        chunk_index: int,
        content: str,
        action_block_id: str = "",
        event_kind: str = "message",
    ) -> ArchiveMemoryRecord:
        identity = "\0".join(
            (
                session_id,
                source_kind,
                source_path,
                str(message_index),
                str(chunk_index),
                action_block_id,
                event_kind,
                content,
            )
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return ArchiveMemoryRecord(
            record_id=f"archive_{digest[:20]}",
            session_id=session_id,
            source_kind=source_kind,
            source_path=source_path,
            role=role,
            message_index=message_index,
            chunk_index=chunk_index,
            content=content,
            created_at=_now(),
            action_block_id=action_block_id,
            event_kind=event_kind,
        )

    @staticmethod
    def _action_block_id(session_id: str, source_path: str, message_index: int) -> str:
        digest = hashlib.sha256(
            f"{session_id}\0{source_path}\0{message_index}".encode("utf-8")
        ).hexdigest()
        return f"action_{digest[:20]}"

    def _chunks(self, text: str) -> list[str]:
        if not text:
            return []
        chunk_limit = self.chunk_chars
        chunks: list[str] = []
        current: list[str] = []
        size = 0
        for line in text.splitlines():
            if len(line) > chunk_limit:
                if current:
                    chunks.append("\n".join(current))
                    current, size = [], 0
                for start in range(0, len(line), chunk_limit):
                    chunks.append(line[start : start + chunk_limit])
                continue
            line_size = len(line) + 1
            if current and size + line_size > chunk_limit:
                chunks.append("\n".join(current))
                current, size = [], 0
            current.append(line)
            size += line_size
        if current:
            chunks.append("\n".join(current))
        return [chunk for chunk in chunks if chunk.strip()]

    def _append(self, records: list[ArchiveMemoryRecord]) -> int:
        if not records:
            return 0
        with MemoryFileLock(self.path):
            existing = {record.record_id for record in self.records()}
            pending = [record for record in records if record.record_id not in existing]
            if not pending:
                return 0
            with self.path.open("a", encoding="utf-8") as handle:
                for record in pending:
                    handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
            self._records_cache_signature = None
            return len(pending)


class StableFactIngestor:
    """High-precision automatic semantic writes from explicit user memory directives."""

    def __init__(
        self,
        store: SemanticMemoryStore,
        evidence: MemoryEvidenceStore | None = None,
        *,
        session_id: str = "",
        source_path: str = "",
        user_scope: str = "",
        channel_scope: str = "",
        workspace_scope: str = "",
    ) -> None:
        self.store = store
        self.evidence = evidence
        self.session_id = session_id
        self.source_path = source_path
        self.user_scope = user_scope
        self.channel_scope = channel_scope
        self.workspace_scope = workspace_scope

    def ingest(self, messages: list[ChatMessage]) -> dict[str, int]:
        stats = {"candidates": 0, "remembered": 0, "duplicates": 0, "conflicts": 0, "rejected": 0}
        seen: set[tuple[str, str]] = set()
        for message in messages:
            if message.role != "user" or memory_write_disallowed(message.content):
                continue
            for line in message.content.splitlines():
                match = _EXPLICIT_MEMORY.match(line)
                if not match:
                    continue
                category = (match.group(1) or "fact").casefold()
                content = match.group(2).strip()
                if historical_state_claim(content):
                    stats['rejected'] += 1
                    continue
                if category == 'preference' or (not match.group(1) and preference_evidence([content], content)):
                    quote = preference_evidence([message.content], line.strip())
                    if quote is None:
                        stats['rejected'] += 1
                        continue
                    category, content = 'preference', quote
                key = (category, content.casefold())
                if key in seen:
                    continue
                seen.add(key)
                stats["candidates"] += 1
                try:
                    result = self.store.remember(category, content, conflict_policy="reject")
                    if "already exists" in result.casefold():
                        stats["duplicates"] += 1
                    else:
                        stats["remembered"] += 1
                    if self.evidence is not None:
                        self.evidence.append(
                            kind="semantic",
                            content=content,
                            session_id=self.session_id,
                            source_path=self.source_path,
                            user_scope=self.user_scope,
                            channel_scope=self.channel_scope,
                            workspace_scope=self.workspace_scope,
                            confidence=1.0,
                            metadata={"category": category, "explicit_user_directive": True,
                                      "evidence": line.strip()},
                        )
                except MemoryConflictError:
                    stats["conflicts"] += 1
                except ValueError:
                    stats["rejected"] += 1
        return stats
