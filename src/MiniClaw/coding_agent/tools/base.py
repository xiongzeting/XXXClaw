from __future__ import annotations

"""Shared tool protocol and result type."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from MiniClaw.cancellation import CancellationToken


@dataclass(slots=True)
class ToolResult:
    content: str
    is_error: bool = False
    details: dict[str, Any] = field(default_factory=dict)


class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]

    async def execute(
        self,
        arguments: dict[str, Any],
        cancellation_token: CancellationToken | None = None,
    ) -> ToolResult: ...
