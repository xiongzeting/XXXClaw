from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from MiniClaw.coding_agent.approval import ApprovalSettings
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.instructions import InstructionConfig
from MiniClaw.coding_agent.memory.manager import MemoryManager
from MiniClaw.coding_agent.runtime import RuntimeSettings
from MiniClaw.llm.types import AssistantReply, ModelEvent, ModelProfile


class OneShotClient:
    def __init__(self) -> None:
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield ModelEvent(type="completed", reply=AssistantReply(content="done"))


class MemoryFinalArchitectureTests(unittest.IsolatedAsyncioTestCase):
    def test_memory_contract_and_skill_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_dir = root / ".aster" / "skills" / "testing"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: testing\ndescription: run focused tests\n"
                "triggers: pytest\n---\nTEST_WORKFLOW\n",
                encoding="utf-8",
            )
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster" / "session.jsonl",
                session_id="s1",
                model_client=OneShotClient(),
                profile=ModelProfile("fake"),
                environment={"MINICLAW_MEMORY_VECTOR_ENABLED": "false"},
            )
            memory, skill = manager.tools()
            self.assertEqual(
                memory.input_schema["properties"]["action"]["enum"],
                ["search", "remember", "replace", "forget"],
            )
            self.assertEqual(skill.name, "skill")
            selected = manager.system_skill_context("run pytest tests")
            self.assertIn("TEST_WORKFLOW", selected)
            manager.semantic.remember("fact", "The testing workflow is stable")
            self.assertTrue(
                all(item.source in {"semantic", "episode", "archive"}
                    for item in manager.retrieve("testing workflow"))
            )

    async def test_replace_is_the_only_update_action_and_uses_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster" / "session.jsonl",
                session_id="s1",
                model_client=OneShotClient(),
                profile=ModelProfile("fake"),
                environment={"MINICLAW_MEMORY_VECTOR_ENABLED": "false"},
            )
            tool = manager.tools()[0]
            await tool.execute({
                "action": "remember",
                "category": "project",
                "content": "Database uses PostgreSQL",
            })
            record_id = next(
                key for key in manager.semantic._metadata()
                if key.startswith("mem_")
            )
            result = await tool.execute({
                "action": "replace",
                "category": "project",
                "recordId": record_id,
                "expectedRevision": 1,
                "content": "Database uses SQLite",
            })
            self.assertFalse(result.is_error)
            self.assertIn("SQLite", json.dumps(manager.semantic.entries(), ensure_ascii=False))

    async def test_memory_mutation_invalidates_both_retrieval_caches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster" / "session.jsonl",
                session_id="s1",
                model_client=OneShotClient(),
                profile=ModelProfile("fake"),
                environment={"MINICLAW_MEMORY_VECTOR_ENABLED": "false"},
            )
            tool = manager.tools()[0]
            await tool.execute({
                "action": "remember", "category": "fact", "content": "old cache fact",
            })
            first = await tool.execute({"action": "search", "query": "old cache fact"})
            self.assertIn("old cache fact", first.content)
            await tool.execute({
                "action": "replace", "category": "fact",
                "oldContent": "old cache fact", "content": "new cache fact",
            })
            second = await tool.execute({"action": "search", "query": "new cache fact"})
            self.assertIn("new cache fact", second.content)

            # Direct store writes are also detected before QueryTracker can
            # serve an old same-query payload.
            manager.semantic.remember("fact", "external cache fact")
            third = await tool.execute({"action": "search", "query": "external cache fact"})
            self.assertIn("external cache fact", third.content)

    async def test_retrieved_memory_enters_user_context_not_system_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = OneShotClient()
            assistant = CodingAssistant(
                client,
                ModelProfile("fake"),
                root,
                runtime_settings=RuntimeSettings(backend="host"),
                instruction_config=InstructionConfig(global_directories=()),
                approval_settings=ApprovalSettings(policy="allow"),
                environment={
                    "MINICLAW_RETRIEVAL_BACKEND": "local",
                    "MINICLAW_SEMANTIC_DISTILL_ENABLED": "false",
                    "MINICLAW_MEMORY_VECTOR_ENABLED": "false",
                },
            )
            assistant.memory.semantic.remember("project", "Database uses PostgreSQL")
            await self._drain(assistant.run("database configuration"))
            request = client.requests[0]
            self.assertNotIn("Database uses PostgreSQL", request.messages[0].content)
            context = [
                message for message in request.messages
                if message.name == "memory_context"
            ]
            self.assertTrue(context)
            self.assertIn("Database uses PostgreSQL", context[0].content)

    async def _drain(self, events) -> None:
        async for _ in events:
            pass


if __name__ == "__main__":
    unittest.main()
