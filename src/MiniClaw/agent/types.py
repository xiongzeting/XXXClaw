from __future__ import annotations

from typing import Any, Protocol

from MiniClaw.cancellation import CancellationToken
from MiniClaw.llm.types import ToolInvocation


class AgentToolResult(Protocol):
    """The result shape consumed by the generic agent loop.

    Concrete coding tools may return their own result class.  Keeping this as
    a structural protocol prevents the agent core from importing product-level
    tool implementations.
    """

    content: str
    is_error: bool
    details: dict[str, Any]


class AgentToolExecutor(Protocol):
    """Minimal tool-host contract required by :class:`AgentLoop`."""

    def definitions(self) -> list[dict[str, Any]]: ...

    async def execute(
        self,
        call: ToolInvocation,
        cancellation_token: CancellationToken | None = None,
    ) -> AgentToolResult: ...


__all__ = ["AgentToolExecutor", "AgentToolResult"]
