from __future__ import annotations

"""Provider-neutral model and message types."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from MiniClaw.cancellation import CancellationToken


Role = Literal["system", "user", "assistant", "tool"]
StopReason = Literal["stop", "tool_calls", "length", "error", "aborted"]


@dataclass(slots=True)
class ToolInvocation:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class ChatMessage:
    role: Role
    content: str = ""
    tool_calls: list[ToolInvocation] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


@dataclass(slots=True)
class AssistantReply:
    content: str = ""
    tool_calls: list[ToolInvocation] = field(default_factory=list)
    stop_reason: StopReason = "stop"
    usage: TokenUsage = field(default_factory=TokenUsage)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ModelProfile:
    model_id: str
    context_window: int = 128_000
    max_output_tokens: int = 8_192
    supports_tools: bool = True
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    cached_input_cost_per_million: float = 0.0


@dataclass(slots=True)
class ModelRequest:
    profile: ModelProfile
    messages: list[ChatMessage]
    tools: list[dict[str, Any]] = field(default_factory=list)
    temperature: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    cancellation_token: CancellationToken | None = None


@dataclass(slots=True)
class ModelEvent:
    type: Literal["text_delta", "tool_call", "usage", "completed", "error", "transport"]
    text: str = ""
    tool_call: ToolInvocation | None = None
    usage: TokenUsage | None = None
    reply: AssistantReply | None = None
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
