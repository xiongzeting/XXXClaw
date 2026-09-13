from __future__ import annotations

"""Shared tool protocol and result type."""

from dataclasses import dataclass, field
from typing import Any, Protocol, Literal

from MiniClaw.cancellation import CancellationToken


@dataclass(slots=True)
class ToolResult:
    content: str
    is_error: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ToolError:
    code: str
    stage: str
    message: str
    retryable: bool = False
    not_started: bool = False
    uncertain_side_effect: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "stage": self.stage, "message": self.message,
                "retryable": self.retryable, "not_started": self.not_started,
                "uncertain_side_effect": self.uncertain_side_effect}


@dataclass(slots=True, frozen=True)
class ToolContext:
    cancellation_token: CancellationToken | None = None
    workspace: str = ""
    session_id: str = ""
    trace_id: str = ""
    timeout_seconds: float | None = None
    approval_state: str = "unknown"
    environment: dict[str, str] = field(default_factory=dict)
    artifact_store: Any = None


@dataclass(slots=True, frozen=True)
class ToolSpec:
    name: str
    side_effect: Literal["none", "reversible", "irreversible", "unknown"] = "unknown"
    idempotent: bool = False
    retry_policy: dict[str, Any] = field(default_factory=dict)


class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]

    async def execute(
        self,
        arguments: dict[str, Any],
        cancellation_token: CancellationToken | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult: ...
