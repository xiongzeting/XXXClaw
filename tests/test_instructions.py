from __future__ import annotations

import os
import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

from MiniClaw.coding_agent.approval import ApprovalSettings
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.instructions import InstructionConfig, ProjectInstructionLoader
from MiniClaw.llm.types import (
    AssistantReply,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    ToolInvocation,
)
from MiniClaw.coding_agent.runtime import RuntimeSettings
from MiniClaw.trace.store import read_trace_records


class ScriptedModelClient:
    def __init__(self, replies: list[AssistantReply]) -> None:
        self.replies = replies
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        yield ModelEvent(type="completed", reply=self.replies.pop(0))


def _system_prompt(request: ModelRequest) -> str:
    return next(message.content for message in request.messages if message.role == "system")


class InstructionLoaderTests(unittest.TestCase):
    def test_global_root_and_nested_rules_are_ordered_and_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            global_dir = base / "global"
            workspace = base / "workspace"
            target = workspace / "src" / "api" / "client.py"
            global_dir.mkdir()
            target.parent.mkdir(parents=True)
            target.write_text("pass\n", encoding="utf-8")
            (global_dir / "AGENTS.md").write_text("GLOBAL_RULE\n", encoding="utf-8")
            (workspace / "AGENTS.MD").write_text("ROOT_RULE\n", encoding="utf-8")
            (workspace / "src" / "AGENTS.md").write_text("SRC_RULE\n", encoding="utf-8")
            (target.parent / "CLAUDE.md").write_text("API_RULE\n", encoding="utf-8")

            loader = ProjectInstructionLoader(
                workspace,
                InstructionConfig(global_directories=(global_dir,), token_budget=2_000),
            )
            loader.activate_path(target)
            resolution = loader.resolve()

            self.assertLess(resolution.prompt.index("GLOBAL_RULE"), resolution.prompt.index("ROOT_RULE"))
            self.assertLess(resolution.prompt.index("ROOT_RULE"), resolution.prompt.index("SRC_RULE"))
            self.assertLess(resolution.prompt.index("SRC_RULE"), resolution.prompt.index("API_RULE"))
            self.assertEqual(
                [source.level for source in resolution.sources],
                ["global", "workspace", "local", "local"],
            )
            self.assertEqual(resolution.sources[-1].display_scope, "src/api")
            with self.assertRaises(PermissionError):
                loader.activate_path(base / "outside.py")

    def test_budget_keeps_nearest_rules_before_global_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            global_dir = base / "global"
            workspace = base / "workspace"
            target = workspace / "src" / "app.py"
            global_dir.mkdir()
            target.parent.mkdir(parents=True)
            target.write_text("pass\n", encoding="utf-8")
            (global_dir / "AGENTS.md").write_text("GLOBAL " * 2_000, encoding="utf-8")
            (workspace / "AGENTS.md").write_text("ROOT " * 500, encoding="utf-8")
            (workspace / "src" / "AGENTS.md").write_text(
                "LOCAL_MUST_BE_PRESERVED\n",
                encoding="utf-8",
            )

            loader = ProjectInstructionLoader(
                workspace,
                InstructionConfig(global_directories=(global_dir,), token_budget=180),
            )
            loader.activate_path(target)
            resolution = loader.resolve()

            local = resolution.sources[-1]
            self.assertEqual(local.status, "included")
            self.assertIn("LOCAL_MUST_BE_PRESERVED", resolution.prompt)
            self.assertLessEqual(resolution.used_tokens, resolution.budget_tokens)
            self.assertTrue(any(source.status == "omitted" for source in resolution.sources[:-1]))

    def test_stat_cache_invalidates_after_instruction_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            agents = workspace / "AGENTS.md"
            agents.write_text("RULE_ONE\n", encoding="utf-8")
            loader = ProjectInstructionLoader(
                workspace,
                InstructionConfig(global_directories=(), token_budget=1_000),
            )
            first = loader.resolve()
            old_stat = agents.stat()
            agents.write_text("RULE_TWO_CHANGED\n", encoding="utf-8")
            os.utime(agents, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 1_000_000))
            second = loader.resolve()

            self.assertNotEqual(first.digest, second.digest)
            self.assertNotEqual(first.sources[0].sha256, second.sources[0].sha256)
            self.assertIn("RULE_TWO_CHANGED", second.prompt)


class InstructionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_activates_local_rules_for_the_next_model_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / "src" / "app.py"
            target.parent.mkdir()
            target.write_text("VALUE = 1\n", encoding="utf-8")
            (target.parent / "AGENTS.md").write_text("READ_LOCAL_RULE\n", encoding="utf-8")
            client = ScriptedModelClient(
                [
                    AssistantReply(
                        tool_calls=[ToolInvocation("read-1", "read", {"path": "src/app.py"})],
                        stop_reason="tool_calls",
                    ),
                    AssistantReply(content="done"),
                ]
            )
            assistant = CodingAssistant(
                client,
                ModelProfile("fake"),
                workspace,
                runtime_settings=RuntimeSettings(backend="host"),
                instruction_config=InstructionConfig(global_directories=()),
                approval_settings=ApprovalSettings(policy="allow"),
            )

            _ = [event async for event in assistant.run("inspect")]

            self.assertNotIn("READ_LOCAL_RULE", _system_prompt(client.requests[0]))
            self.assertIn("READ_LOCAL_RULE", _system_prompt(client.requests[1]))

    async def test_first_write_is_paused_until_new_local_rules_are_injected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target_dir = workspace / "src"
            target_dir.mkdir()
            (target_dir / "AGENTS.md").write_text("WRITE_LOCAL_RULE\n", encoding="utf-8")
            write_call = ToolInvocation(
                "write-1",
                "write",
                {"path": "src/new.py", "content": "VALUE = 2\n"},
            )
            client = ScriptedModelClient(
                [
                    AssistantReply(tool_calls=[write_call], stop_reason="tool_calls"),
                    AssistantReply(
                        tool_calls=[
                            ToolInvocation(
                                "write-2",
                                "write",
                                {"path": "src/new.py", "content": "VALUE = 2\n"},
                            )
                        ],
                        stop_reason="tool_calls",
                    ),
                    AssistantReply(content="done"),
                ]
            )
            assistant = CodingAssistant(
                client,
                ModelProfile("fake"),
                workspace,
                runtime_settings=RuntimeSettings(backend="host"),
                instruction_config=InstructionConfig(global_directories=()),
                approval_settings=ApprovalSettings(policy="allow"),
            )

            events = [event async for event in assistant.run("write it")]

            tool_events = [event for event in events if event.type == "tool_finished"]
            self.assertTrue(tool_events[0].is_error)
            self.assertTrue(tool_events[0].details["instructions_refresh_required"])
            self.assertFalse(tool_events[1].is_error)
            self.assertEqual((target_dir / "new.py").read_text(encoding="utf-8"), "VALUE = 2\n")
            self.assertNotIn("WRITE_LOCAL_RULE", _system_prompt(client.requests[0]))
            self.assertIn("WRITE_LOCAL_RULE", _system_prompt(client.requests[1]))

            records = read_trace_records(workspace / ".aster" / "trace.jsonl")
            instruction_events = [
                record for record in records if record["type"] == "instructions.injected"
            ]
            tool_records = [record for record in records if record["type"] == "tool.call"]
            self.assertEqual([record["data"]["status"] for record in tool_records], ["blocked", "success"])
            self.assertGreaterEqual(len(instruction_events), 2)
            local_source = next(
                source
                for record in instruction_events
                for source in record["data"]["sources"]
                if source["path"] == "src/AGENTS.md"
            )
            self.assertEqual(local_source["status"], "included")
            self.assertIn("sha256", local_source)

    async def test_snapshot_loads_instructions_from_effective_task_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "source"
            workspace.mkdir()
            (workspace / "AGENTS.md").write_text("SNAPSHOT_RULE\n", encoding="utf-8")
            assistant = CodingAssistant(
                ScriptedModelClient([AssistantReply(content="done")]),
                ModelProfile("fake"),
                workspace,
                runtime_settings=RuntimeSettings(backend="host", workspace_mode="snapshot"),
                instruction_config=InstructionConfig(global_directories=()),
                approval_settings=ApprovalSettings(policy="allow"),
            )

            self.assertNotEqual(assistant.runtime.host_workspace, workspace)
            self.assertEqual(
                assistant.instruction_loader.workspace_root,
                assistant.runtime.host_workspace,
            )
            self.assertIn("SNAPSHOT_RULE", assistant.instruction_loader.resolve().prompt)


if __name__ == "__main__":
    unittest.main()
