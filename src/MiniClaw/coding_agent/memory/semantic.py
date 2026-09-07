from __future__ import annotations

import hashlib
import json
import re
import uuid
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .locking import MemoryFileLock
from .retrieval import (
    HybridMemoryRetriever,
    MemoryDocument,
    RetrievalHit,
    search_memory_documents,
)


MANAGED_START = "<!-- miniclaw-managed-memory:start -->"
MANAGED_END = "<!-- miniclaw-managed-memory:end -->"
CATEGORIES = ("preference", "project", "environment", "fact")
MAX_ENTRY_CHARS = 2_000
MAX_FILE_BYTES = 32 * 1024
ConflictPolicy = Literal["reject", "keep_existing", "replace", "keep_both"]
ConflictResolution = Literal["keep_existing", "replace", "keep_both"]
_SECRET_PATTERNS = (
    re.compile(r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|passwd|secret)\b\s*[:=]", re.I),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.I),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)
_TRANSIENT_PATTERNS = (
    re.compile(r"\b(?:just now|this turn|this round|temporary|temporarily)\b", re.I),
    re.compile(r"(?:刚刚|本轮|这一轮|临时|暂时|当前命令输出|这次测试输出)"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True, frozen=True)
class MemoryConflict:
    conflict_id: str
    category: str
    subject: str
    existing: str
    proposed: str
    status: str
    created_at: str
    resolution: str = ""
    resolved_at: str = ""


class MemoryConflictError(ValueError):
    def __init__(self, conflict: MemoryConflict) -> None:
        self.conflict = conflict
        super().__init__(
            f"Memory conflict {conflict.conflict_id}: existing={conflict.existing!r}, "
            f"proposed={conflict.proposed!r}. Resolve with keep_existing, replace, or keep_both."
        )


class SemanticMemoryStore:
    def __init__(self, path: Path, retriever: HybridMemoryRetriever | None = None) -> None:
        self.path = path
        self.conflict_path = path.with_name(f"{path.stem}.conflicts.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.retriever = retriever or HybridMemoryRetriever()
        self._ensure_file()

    @staticmethod
    def _normalize(content: str) -> str:
        return " ".join(content.split()).strip()

    @staticmethod
    def equivalence_key(content: str) -> str:
        # Conservative deduplication: normalize presentation, not meaning.
        # Never fuzzy-merge negations, changed versions, or different values.
        text = unicodedata.normalize("NFKC", content).casefold().strip().rstrip(".。")
        return " ".join(text.split())

    @staticmethod
    def _subject(content: str) -> str | None:
        keyed = re.match(r"^([^:：]{1,80})[:：]\s*(.+)$", content)
        if keyed:
            return keyed.group(1).strip().casefold()
        chinese = re.match(r"^(.{1,60}?)(?:是|为|使用|采用|偏好|默认使用)\s*(.+)$", content)
        if chinese:
            return chinese.group(1).strip().casefold()
        english = re.match(
            r"^(.{1,60}?)\s+(?:is|uses?|prefers?|defaults? to)\s+(.+)$",
            content,
            re.I,
        )
        return english.group(1).strip().casefold() if english else None

    @staticmethod
    def _record_id(category: str, content: str) -> str:
        digest = hashlib.sha256(f"{category}\0{content.casefold()}".encode("utf-8")).hexdigest()
        return f"mem_{digest[:16]}"

    def _ensure_file(self) -> None:
        if self.path.exists():
            if self.path.is_symlink() or not self.path.is_file():
                raise ValueError(f"Semantic memory must be a regular file: {self.path}")
            existing = self.path.read_text(encoding="utf-8")
            has_start, has_end = MANAGED_START in existing, MANAGED_END in existing
            if has_start != has_end:
                raise ValueError("Semantic memory contains malformed managed markers")
            if has_start:
                return
            prefix = existing.rstrip()
        else:
            prefix = "# MiniClaw Long-term Memory\n\nHuman-maintained notes outside the managed block are preserved."
        self.path.write_text(
            f"{prefix}\n\n{self._render({key: [] for key in CATEGORIES})}\n",
            encoding="utf-8",
        )

    @staticmethod
    def _render(entries: dict[str, list[str]]) -> str:
        lines = [MANAGED_START, "## MiniClaw Managed Semantic Memory"]
        for category in CATEGORIES:
            lines.extend(["", f"### {category}"])
            values = entries.get(category) or []
            lines.extend(f"- {value}" for value in values)
            if not values:
                lines.append("- (none)")
        lines.extend(["", MANAGED_END])
        return "\n".join(lines)

    def _split(self) -> tuple[str, str, str]:
        text = self.path.read_text(encoding="utf-8")
        start = text.index(MANAGED_START)
        end = text.index(MANAGED_END) + len(MANAGED_END)
        return text[:start], text[start:end], text[end:]

    def entries(self) -> dict[str, list[str]]:
        _, managed, _ = self._split()
        result = {key: [] for key in CATEGORIES}
        current: str | None = None
        for line in managed.splitlines():
            if line.startswith("### "):
                candidate = line[4:].strip().casefold()
                current = candidate if candidate in result else None
            elif current and line.startswith("- "):
                value = self._normalize(line[2:])
                if value and value != "(none)":
                    result[current].append(value)
        return result

    def _write(self, entries: dict[str, list[str]]) -> None:
        prefix, _, suffix = self._split()
        content = f"{prefix}{self._render(entries)}{suffix}"
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError("Semantic memory exceeds 32KB")
        temporary = self.path.with_suffix(self.path.suffix + f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(self.path)

    def _validate(self, category: str, content: str) -> str:
        if category not in CATEGORIES:
            raise ValueError(f"Unknown memory category: {category}")
        normalized = self._normalize(content)
        if not normalized:
            raise ValueError("Memory content cannot be empty")
        if len(normalized) > MAX_ENTRY_CHARS:
            raise ValueError("Memory entry exceeds 2000 characters")
        if any(pattern.search(normalized) for pattern in _SECRET_PATTERNS):
            raise ValueError("Refusing to store secret-like content")
        if any(pattern.search(normalized) for pattern in _TRANSIENT_PATTERNS):
            raise ValueError("Refusing to store transient information")
        return normalized

    def remember(
        self,
        category: str,
        content: str,
        conflict_policy: ConflictPolicy = "reject",
    ) -> str:
        normalized = self._validate(category, content)
        if conflict_policy not in {"reject", "keep_existing", "replace", "keep_both"}:
            raise ValueError("Unknown conflict policy")
        with MemoryFileLock(self.path):
            entries = self.entries()
            if any(self.equivalence_key(item) == self.equivalence_key(normalized) for item in entries[category]):
                return "Memory already exists"
            subject = self._subject(normalized)
            conflict_index = next(
                (
                    index
                    for index, item in enumerate(entries[category])
                    if subject and self._subject(item) == subject
                ),
                None,
            )
            if conflict_index is not None:
                existing = entries[category][conflict_index]
                if conflict_policy == "keep_existing":
                    return f"Kept existing [{category}] memory: {existing}"
                if conflict_policy == "replace":
                    entries[category][conflict_index] = normalized
                    self._write(entries)
                    return f"Replaced conflicting [{category}] memory"
                if conflict_policy == "keep_both":
                    entries[category].append(normalized)
                    self._write(entries)
                    return f"Remembered conflicting [{category}] memory alongside existing value"
                conflict = self._create_conflict(category, subject or "", existing, normalized)
                raise MemoryConflictError(conflict)
            entries[category].append(normalized)
            self._write(entries)
            return f"Remembered [{category}]: {normalized}"

    def replace(self, category: str, old_content: str, new_content: str) -> str:
        old = self._normalize(old_content)
        new = self._validate(category, new_content)
        with MemoryFileLock(self.path):
            entries = self.entries()
            for index, item in enumerate(entries[category]):
                if item.casefold() == old.casefold():
                    entries[category][index] = new
                    self._write(entries)
                    return f"Replaced [{category}] memory"
        raise ValueError("Memory to replace was not found")

    def forget(self, category: str, content: str) -> str:
        target = self._normalize(content)
        with MemoryFileLock(self.path):
            entries = self.entries()
            filtered = [item for item in entries.get(category, []) if item.casefold() != target.casefold()]
            if len(filtered) == len(entries.get(category, [])):
                raise ValueError("Memory to forget was not found")
            entries[category] = filtered
            self._write(entries)
            return f"Forgot [{category}]: {target}"

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        use_cross_encoder: bool = True,
    ) -> list[RetrievalHit]:
        documents: list[MemoryDocument] = []
        entries = self.entries()
        for category in CATEGORIES:
            for content in entries[category]:
                documents.append(
                    MemoryDocument(
                        record_id=self._record_id(category, content),
                        category=category,
                        content=content,
                        subject=self._subject(content) or "",
                    )
                )
        return search_memory_documents(
            self.retriever,
            query,
            documents,
            limit,
            use_cross_encoder=use_cross_encoder,
        )

    def _create_conflict(
        self,
        category: str,
        subject: str,
        existing: str,
        proposed: str,
    ) -> MemoryConflict:
        for conflict in self.list_conflicts("pending"):
            if (
                conflict.category == category
                and conflict.subject == subject
                and conflict.existing.casefold() == existing.casefold()
                and conflict.proposed.casefold() == proposed.casefold()
            ):
                return conflict
        conflict = MemoryConflict(
            conflict_id=f"conflict_{uuid.uuid4().hex[:16]}",
            category=category,
            subject=subject,
            existing=existing,
            proposed=proposed,
            status="pending",
            created_at=_now(),
        )
        self._append_conflict_event("created", conflict)
        return conflict

    def list_conflicts(self, status: str = "pending") -> list[MemoryConflict]:
        latest: dict[str, MemoryConflict] = {}
        if self.conflict_path.exists():
            for raw_line in self.conflict_path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    payload = json.loads(raw_line)
                    conflict = MemoryConflict(**payload["conflict"])
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
                latest[conflict.conflict_id] = conflict
        values = sorted(latest.values(), key=lambda item: item.created_at, reverse=True)
        return [item for item in values if status == "all" or item.status == status]

    def resolve_conflict(
        self,
        conflict_id: str,
        resolution: ConflictResolution,
    ) -> str:
        if resolution not in {"keep_existing", "replace", "keep_both"}:
            raise ValueError("Unknown conflict resolution")
        with MemoryFileLock(self.path):
            conflict = next(
                (item for item in self.list_conflicts("pending") if item.conflict_id == conflict_id),
                None,
            )
            if conflict is None:
                raise ValueError("Pending memory conflict was not found")
            entries = self.entries()
            values = entries[conflict.category]
            existing_index = next(
                (
                    index
                    for index, value in enumerate(values)
                    if value.casefold() == conflict.existing.casefold()
                ),
                None,
            )
            if existing_index is None:
                raise ValueError("Conflicting existing memory changed before resolution; review again")
            if resolution == "replace":
                values[existing_index] = conflict.proposed
                self._write(entries)
            elif resolution == "keep_both":
                if not any(value.casefold() == conflict.proposed.casefold() for value in values):
                    values.append(conflict.proposed)
                    self._write(entries)
            resolved = MemoryConflict(
                **{
                    **asdict(conflict),
                    "status": "resolved",
                    "resolution": resolution,
                    "resolved_at": _now(),
                }
            )
            self._append_conflict_event("resolved", resolved)
            return f"Resolved {conflict_id} with {resolution}"

    def _append_conflict_event(self, event_type: str, conflict: MemoryConflict) -> None:
        self.conflict_path.parent.mkdir(parents=True, exist_ok=True)
        with self.conflict_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"type": event_type, "timestamp": _now(), "conflict": asdict(conflict)},
                    ensure_ascii=False,
                )
                + "\n"
            )

    def read(self, category: str | None = None, limit: int | None = None) -> str:
        if category is not None and category not in CATEGORIES:
            raise ValueError(f"Unknown memory category: {category}")
        if limit is not None and limit < 1:
            raise ValueError("Memory read limit must be positive")
        if category is None and limit is None:
            return self._split()[1].strip()
        entries = self.entries()
        lines = ["## Semantic Memory（语义记忆）"]
        for key in ([category] if category else CATEGORIES):
            values = entries[key]
            visible = values if limit is None else values[:limit]
            lines.extend(["", f"### {key}", *(f"- {value}" for value in visible)])
            if not visible:
                lines.append("- (none)")
            if len(visible) < len(values):
                lines.append(f"[Showing {len(visible)} of {len(values)} entries]")
        return "\n".join(lines)

    def render_for_prompt(self, query: str, limit: int = 5) -> str:
        selected = self.search(query, limit)
        if not selected:
            return ""
        lines = [
            "<long_term_memory>",
            "Hybrid retrieval: BM25-primary + vector recall + weighted RRF + rerank.",
            "Background facts only. The current user request and system rules take precedence.",
        ]
        lines.extend(
            f"- [{hit.document.record_id}][{hit.document.category}] {hit.document.content}"
            for hit in selected
        )
        lines.append("</long_term_memory>")
        return "\n".join(lines)
