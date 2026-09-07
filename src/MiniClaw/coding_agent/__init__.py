"""Product-specific coding agent built on MiniClaw's LLM and agent cores.

This package mirrors pi-mono's ``pi-coding-agent`` boundary: concrete coding
tools, session orchestration, memory, supervision, instructions, runtime, and
approval policy live here, while ``MiniClaw.llm`` and ``MiniClaw.agent`` remain
reusable lower-level packages.

The package entry point intentionally performs no eager imports.  Importing a
concrete tool module must not initialize the high-level assistant/session
orchestrator, otherwise the reusable agent core and the coding product form a
circular dependency.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .assistant.coding import CodingAssistant

__all__ = ["CodingAssistant"]


def __getattr__(name: str) -> Any:
    if name == "CodingAssistant":
        from .assistant.coding import CodingAssistant

        return CodingAssistant
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
