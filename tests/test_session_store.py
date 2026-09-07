from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from MiniClaw.coding_agent.assistant.session import JsonlSessionStore
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.llm.types import ChatMessage, ToolInvocation


class SessionStoreTests(unittest.TestCase):
    def test_append_and_recover_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            store = JsonlSessionStore(path)
            store.append(ChatMessage(role="user", content="hello"))
            store.append(
                ChatMessage(
                    role="assistant",
                    tool_calls=[ToolInvocation("call-1", "read", {"path": "a.txt"})],
                )
            )
            with path.open("a", encoding="utf-8") as handle:
                handle.write("{broken")

            loaded = store.load()
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[1].tool_calls[0].name, "read")

    def test_interrupted_tool_call_gets_protocol_repair_message(self) -> None:
        messages = [
            ChatMessage(
                role="assistant",
                tool_calls=[ToolInvocation("call-1", "bash", {"command": "work"})],
            )
        ]
        repairs = CodingAssistant._repair_interrupted_tool_calls(messages)
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0].role, "tool")
        self.assertEqual(repairs[0].tool_call_id, "call-1")
        self.assertIn("inspect current workspace state", repairs[0].content)


if __name__ == "__main__":
    unittest.main()
