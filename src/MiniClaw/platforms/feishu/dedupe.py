from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from MiniClaw.coding_agent.memory.locking import MemoryFileLock


class PersistentEventDeduplicator:
    """Durable inbound inbox with duplicate suppression and stale-work recovery."""

    def __init__(
        self,
        path: Path,
        max_entries: int = 10_000,
        stale_after_seconds: float = 300,
    ) -> None:
        self.path = path
        self.max_entries = max_entries
        self.stale_after = timedelta(seconds=max(1.0, stale_after_seconds))
        self._order: deque[str] = deque()
        self._seen: set[str] = set()
        self._status: dict[str, str] = {}
        self._updated_at: dict[str, datetime] = {}
        if path.exists():
            for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_entries:]:
                try:
                    record = json.loads(raw_line)
                    event_id = str(record.get("event_id") or "")
                except (json.JSONDecodeError, TypeError):
                    continue
                if event_id:
                    self._remember(event_id)
                    self._status[event_id] = str(record.get("status") or "completed")
                    self._updated_at[event_id] = _timestamp(record.get("updated_at"))

    def claim(self, event_id: str) -> bool:
        if not event_id:
            return False
        status = self._status.get(event_id)
        if status == "completed":
            return False
        if status == "processing" and not self._stale(event_id):
            return False
        self._remember(event_id)
        self._status[event_id] = "processing"
        self._updated_at[event_id] = datetime.now(timezone.utc)
        self._append(event_id, "processing")
        return True

    def complete(self, event_id: str) -> None:
        if not event_id:
            return
        self._remember(event_id)
        self._status[event_id] = "completed"
        self._updated_at[event_id] = datetime.now(timezone.utc)
        self._append(event_id, "completed")

    def fail(self, event_id: str, error: str) -> None:
        if not event_id:
            return
        self._remember(event_id)
        self._status[event_id] = "failed"
        self._updated_at[event_id] = datetime.now(timezone.utc)
        self._append(event_id, "failed", error=error)

    def _append(self, event_id: str, status: str, *, error: str | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with MemoryFileLock(self.path):
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "event_id": event_id,
                            "status": status,
                            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "error": error,
                        }
                    )
                    + "\n"
                )

    def _stale(self, event_id: str) -> bool:
        updated_at = self._updated_at.get(event_id)
        return updated_at is None or datetime.now(timezone.utc) - updated_at >= self.stale_after

    def _remember(self, event_id: str) -> None:
        if event_id in self._seen:
            return
        self._seen.add(event_id)
        self._order.append(event_id)
        while len(self._order) > self.max_entries:
            removed = self._order.popleft()
            self._seen.discard(removed)
            self._status.pop(removed, None)
            self._updated_at.pop(removed, None)


def _timestamp(value: object) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)
