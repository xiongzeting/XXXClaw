from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from MiniClaw.llm.types import ChatMessage, TokenUsage, ToolInvocation


@dataclass(slots=True)
class AgentEvent:
    type: Literal[
        "run_started",
        "turn_started",
        "message_added",
        "text_delta",
        "tool_started",
        "tool_finished",
        "turn_finished",
        "run_finished",
        "error",
    ]
    message: ChatMessage | None = None
    text: str = ""
    tool_call: ToolInvocation | None = None
    tool_result: str | None = None
    is_error: bool = False
    usage: TokenUsage | None = None
    details: dict[str, Any] | None = None
