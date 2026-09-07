from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .locking import MemoryFileLock


_SECRET_PATTERNS = (
    re.compile(
        r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|passwd|secret)\b\s*[:=]",
        re.IGNORECASE,
    ),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalized(value: str) -> str:
    return " ".join(value.split()).strip()


def contains_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


@dataclass(slots=True, frozen=True)
class MemoryEvidenceRecord:
    """One auditable memory item with a pointer back to its source evidence.

    The existing human-readable Semantic/Episodic/Skill files remain supported.
    This append-only ledger is the canonical provenance layer shared by those
    views, so a summary or fact never has to pretend it is the original source.
    """

    record_id: str
    kind: str
    content: str
    session_id: str
    source_path: str = ""
    parent_id: str = ""
    user_scope: str = ""
    channel_scope: str = ""
    workspace_scope: str = ""
    confidence: float = 1.0
    status: str = "active"
    created_at: str = field(default_factory=_now)
    expires_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        if self.status.casefold() != "active":
            return False
        if not self.expires_at.strip():
            return True
        try:
            expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        return expires > datetime.now(timezone.utc)


class MemoryEvidenceStore:
    """Append-only memory ledger with exact deduplication and safe compaction."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records_cache_signature: tuple[int, int] | None = None
        self._records_cache: tuple[MemoryEvidenceRecord, ...] = ()

    @staticmethod
    def _identity(
        *,
        kind: str,
        content: str,
        session_id: str,
        source_path: str,
        user_scope: str,
        channel_scope: str,
        workspace_scope: str,
    ) -> str:
        value = "\0".join(
            (
                kind.casefold(),
                _normalized(content).casefold(),
                session_id,
                source_path,
                user_scope,
                channel_scope,
                workspace_scope,
            )
        )
        return f"evidence_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:20]}"

    def append(
        self,
        *,
        kind: str,
        content: str,
        session_id: str,
        source_path: str = "",
        parent_id: str = "",
        user_scope: str = "",
        channel_scope: str = "",
        workspace_scope: str = "",
        confidence: float = 1.0,
        status: str = "active",
        expires_at: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEvidenceRecord | None:
        normalized = _normalized(content)
        if not normalized or contains_secret(normalized):
            return None
        record = MemoryEvidenceRecord(
            record_id=self._identity(
                kind=kind,
                content=normalized,
                session_id=session_id,
                source_path=source_path,
                user_scope=user_scope,
                channel_scope=channel_scope,
                workspace_scope=workspace_scope,
            ),
            kind=kind,
            content=normalized,
            session_id=session_id,
            source_path=source_path,
            parent_id=parent_id,
            user_scope=user_scope,
            channel_scope=channel_scope,
            workspace_scope=workspace_scope,
            confidence=max(0.0, min(1.0, float(confidence))),
            status=status,
            expires_at=expires_at,
            metadata=dict(metadata or {}),
        )
        with MemoryFileLock(self.path):
            existing = {item.record_id for item in self.records()}
            if record.record_id in existing:
                return None
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
            self._records_cache_signature = None
        return record

    def records(
        self,
        *,
        kinds: Iterable[str] | None = None,
        session_ids: Iterable[str] | None = None,
    ) -> list[MemoryEvidenceRecord]:
        if not self.path.exists():
            self._records_cache_signature = None
            self._records_cache = ()
            return []
        stat = self.path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature != self._records_cache_signature:
            values: list[MemoryEvidenceRecord] = []
            for raw_line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    payload = json.loads(raw_line)
                    values.append(
                        MemoryEvidenceRecord(
                            record_id=str(payload["record_id"]),
                            kind=str(payload["kind"]),
                            content=str(payload["content"]),
                            session_id=str(payload["session_id"]),
                            source_path=str(payload.get("source_path") or ""),
                            parent_id=str(payload.get("parent_id") or ""),
                            user_scope=str(payload.get("user_scope") or ""),
                            channel_scope=str(payload.get("channel_scope") or ""),
                            workspace_scope=str(payload.get("workspace_scope") or ""),
                            confidence=float(payload.get("confidence", 1.0)),
                            status=str(payload.get("status") or "active"),
                            created_at=str(payload.get("created_at") or _now()),
                            expires_at=str(payload.get("expires_at") or ""),
                            metadata=dict(payload.get("metadata") or {}),
                        )
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
            self._records_cache = tuple(values)
            self._records_cache_signature = signature
        kind_filter = {value for value in kinds or ()}
        session_filter = {value for value in session_ids or ()}
        values: list[MemoryEvidenceRecord] = []
        for record in self._records_cache:
            if kind_filter and record.kind not in kind_filter:
                continue
            if session_filter and record.session_id not in session_filter:
                continue
            values.append(record)
        return values

    def compact(self, max_records: int = 100_000) -> dict[str, int]:
        """Remove exact duplicate/inactive ledger events while keeping newest data."""

        max_records = max(1, max_records)
        values = self.records()
        latest: dict[str, MemoryEvidenceRecord] = {}
        for record in values:
            latest[record.record_id] = record
        active = [record for record in latest.values() if record.status == "active"]
        active.sort(key=lambda record: record.created_at)
        active = active[-max_records:]
        if not self.path.exists() and not active:
            return {"before": 0, "after": 0}
        temporary = self.path.with_suffix(self.path.suffix + f".{uuid.uuid4().hex}.tmp")
        with MemoryFileLock(self.path):
            with temporary.open("w", encoding="utf-8") as handle:
                for record in active:
                    handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
            temporary.replace(self.path)
            self._records_cache_signature = None
        return {"before": len(values), "after": len(active)}
