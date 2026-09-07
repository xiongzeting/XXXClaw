from __future__ import annotations

import os
import time
from pathlib import Path


class MemoryFileLock:
    """Small cross-process lock using exclusive file creation."""

    def __init__(self, target: Path, timeout_seconds: float = 3.0, stale_seconds: float = 30.0) -> None:
        self.path = target.with_suffix(target.suffix + ".lock")
        self.timeout_seconds = timeout_seconds
        self.stale_seconds = stale_seconds
        self._owned = False

    def __enter__(self) -> "MemoryFileLock":
        deadline = time.monotonic() + self.timeout_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
                os.close(descriptor)
                self._owned = True
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                    if age > self.stale_seconds:
                        self.path.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for semantic memory lock: {self.path}")
                time.sleep(0.05)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._owned:
            self.path.unlink(missing_ok=True)
            self._owned = False
