from __future__ import annotations

"""Serialize writes and edits that target the same absolute file path."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar


T = TypeVar("T")


@dataclass(slots=True)
class _QueueEntry:
    lock: asyncio.Lock
    users: int = 0


_queues: dict[str, _QueueEntry] = {}


def _queue_key(path: Path) -> str:
    return str(path.resolve()).casefold()


async def with_file_mutation_queue(path: Path, operation: Callable[[], Awaitable[T]]) -> T:
    key = _queue_key(path)
    entry = _queues.get(key)
    if entry is None:
        entry = _QueueEntry(asyncio.Lock())
        _queues[key] = entry
    entry.users += 1
    try:
        async with entry.lock:
            return await operation()
    finally:
        entry.users -= 1
        if entry.users == 0 and not entry.lock.locked() and _queues.get(key) is entry:
            del _queues[key]
