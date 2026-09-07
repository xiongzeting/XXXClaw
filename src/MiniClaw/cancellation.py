from __future__ import annotations

import asyncio
import time
from typing import Any


class OperationCancelledError(RuntimeError):
    """A cooperative MiniClaw operation stopped because its shared token was cancelled."""

    def __init__(
        self,
        reason: str,
        *,
        stage: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(reason)
        self.stage = stage
        self.details = dict(details or {})


class ModelCancelledError(OperationCancelledError):
    def __init__(self, reason: str, partial_reply=None, **kwargs: Any) -> None:
        super().__init__(reason, **kwargs)
        self.partial_reply = partial_reply


class ToolCancelledError(OperationCancelledError):
    pass


class CancellationToken:
    """One cooperative cancellation signal shared by an entire Agent attempt."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason = "Operation cancelled"
        self._cancelled_at: float | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def cancelled_at(self) -> float | None:
        return self._cancelled_at

    def cancel(self, reason: str = "Operation cancelled") -> bool:
        if self._event.is_set():
            return False
        self._reason = reason.strip() or "Operation cancelled"
        self._cancelled_at = time.time()
        self._event.set()
        return True

    async def wait(self) -> None:
        await self._event.wait()

    def raise_if_cancelled(self, *, stage: str | None = None) -> None:
        if self.cancelled:
            raise OperationCancelledError(self.reason, stage=stage)

    def raise_if_tool_cancelled(self, *, stage: str | None = None) -> None:
        if self.cancelled:
            raise ToolCancelledError(self.reason, stage=stage)

