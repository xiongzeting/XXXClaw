from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from MiniClaw.coding_agent.memory.locking import MemoryFileLock


T = TypeVar("T")


@dataclass(slots=True)
class _DeliveryLock:
    lock: asyncio.Lock
    users: int = 0


class DeliveryManager:
    """Small durable outbox with idempotency and bounded in-process retries."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_attempts: int = 3,
        retry_base_seconds: float = 0.25,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self.path = Path(path)
        self.max_attempts = max_attempts
        self.retry_base_seconds = max(0.0, retry_base_seconds)
        self._locks: dict[str, _DeliveryLock] = {}
        self._locks_guard = threading.Lock()

    async def deliver(
        self,
        idempotency_key: str,
        operation: str,
        payload: dict[str, Any],
        sender: Callable[[], Awaitable[T]],
    ) -> T | None:
        with self._locks_guard:
            entry = self._locks.setdefault(idempotency_key, _DeliveryLock(asyncio.Lock()))
            entry.users += 1
        try:
            async with entry.lock:
                previous = self.latest(idempotency_key)
                if previous and previous.get("status") == "delivered":
                    return previous.get("result")  # type: ignore[return-value]
                self._append(
                    idempotency_key,
                    operation,
                    "queued",
                    attempt=0,
                    payload=payload,
                )
                last_error: Exception | None = None
                for attempt in range(1, self.max_attempts + 1):
                    self._append(
                        idempotency_key,
                        operation,
                        "attempting",
                        attempt=attempt,
                        payload=payload,
                    )
                    try:
                        result = await sender()
                    except Exception as exc:
                        last_error = exc
                        terminal = attempt >= self.max_attempts
                        self._append(
                            idempotency_key,
                            operation,
                            "failed" if terminal else "retrying",
                            attempt=attempt,
                            payload=payload,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                        if terminal:
                            raise
                        await asyncio.sleep(self.retry_base_seconds * (2 ** (attempt - 1)))
                        continue
                    self._append(
                        idempotency_key,
                        operation,
                        "delivered",
                        attempt=attempt,
                        payload=payload,
                        result=result,
                    )
                    return result
                if last_error is not None:
                    raise last_error
                return None
        finally:
            with self._locks_guard:
                entry.users -= 1
                if entry.users == 0 and self._locks.get(idempotency_key) is entry:
                    self._locks.pop(idempotency_key, None)

    def latest(self, idempotency_key: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        latest: dict[str, Any] | None = None
        for raw_line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("idempotency_key") == idempotency_key:
                latest = record
        return latest

    def _append(
        self,
        idempotency_key: str,
        operation: str,
        status: str,
        *,
        attempt: int,
        payload: dict[str, Any],
        result: Any = None,
        error: str | None = None,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "idempotency_key": idempotency_key,
            "operation": operation,
            "status": status,
            "attempt": attempt,
            "payload": payload,
            "result": result,
            "error": error,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with MemoryFileLock(self.path):
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
