from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from MiniClaw.llm.types import ChatMessage, ToolInvocation


class JsonlSessionStore:
    """Append-only transcript storage. Partial final lines are ignored on recovery."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, message: ChatMessage) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(message), ensure_ascii=False) + "\n")

    def load(self) -> list[ChatMessage]:
        if not self.path.exists():
            return []
        messages: list[ChatMessage] = []
        for raw_line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                payload = json.loads(raw_line)
                payload["tool_calls"] = [ToolInvocation(**call) for call in payload.get("tool_calls") or []]
                messages.append(ChatMessage(**payload))
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
        return messages
