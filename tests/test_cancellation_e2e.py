from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

from MiniClaw.coding_agent.approval import ApprovalSettings
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.cancellation import CancellationToken, ToolCancelledError
from MiniClaw.llm.types import (
    AssistantReply,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    ToolInvocation,
)
from MiniClaw.coding_agent.runtime.config import RuntimeSettings
from MiniClaw.coding_agent.runtime.execution import DockerCommandExecutor
from MiniClaw.coding_agent.runtime.workspace import WorkspaceGuard
from MiniClaw.coding_agent.tools.executor import ToolExecutor
from MiniClaw.coding_agent.tools.write import WriteTool
from MiniClaw.trace.analysis import generate_dashboard
from MiniClaw.trace.store import read_trace_records


class OneReplyModelClient:
    def __init__(self, reply: AssistantReply) -> None:
        self.reply = reply

    async def stream(self, _request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield ModelEvent(type="completed", reply=self.reply)


class _EmptyStream:
    async def read(self, _size: int = -1) -> bytes:
        return b""


class _PendingProcess:
    def __init__(self) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self.stdout = _EmptyStream()
        self.stderr = _EmptyStream()
        self._finished = asyncio.Event()

    async def wait(self) -> int:
        await self._finished.wait()
        return self.returncode or 0

    def kill(self) -> None:
        self.returncode = -9
        self._finished.set()


class _ObservedDockerExecutor(DockerCommandExecutor):
    def __init__(self, settings, workspace, session_dir, task_id) -> None:
        super().__init__(settings, workspace, session_dir, task_id)
        self.removed: list[str] = []

    async def _remove_container(self, container_name: str) -> None:
        self.removed.append(container_name)


class EndToEndCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_kills_host_process_blocks_followup_tool_and_marks_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            executable = subprocess.list2cmdline([sys.executable])
            command = (
                f'{executable} -c "from pathlib import Path; import time; '
                "Path('started.txt').write_text('started'); time.sleep(5); "
                "Path('late.txt').write_text('late')\""
            )
            model = OneReplyModelClient(
                AssistantReply(
                    tool_calls=[
                        ToolInvocation("bash-1", "bash", {"command": command}),
                        ToolInvocation(
                            "write-2",
                            "write",
                            {"path": "followup.txt", "content": "must not run"},
                        ),
                    ],
                    stop_reason="tool_calls",
                )
            )
            assistant = CodingAssistant(
                model,
                ModelProfile("fake"),
                workspace,
                runtime_settings=RuntimeSettings(backend="host"),
                approval_settings=ApprovalSettings(policy="allow"),
            )
            assistant.create_goal("cancel safely", ["no late writes"])

            async def collect():
                return [event async for event in assistant.run_goal("start")]

            task = asyncio.create_task(collect())
            for _ in range(200):
                if (workspace / "started.txt").exists():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue((workspace / "started.txt").exists())
            self.assertTrue(assistant.cancel("user requested cancellation"))
            events = await asyncio.wait_for(task, timeout=4)
            await asyncio.sleep(0.1)

            self.assertFalse((workspace / "late.txt").exists())
            self.assertFalse((workspace / "followup.txt").exists())
            self.assertEqual(assistant.goal_store.read().status, "cancelled")  # type: ignore[union-attr]
            self.assertEqual(events[-1].details["stop_reason"], "aborted")
            cancelled_tools = [event for event in events if event.type == "tool_finished"]
            self.assertEqual(len(cancelled_tools), 2)
            self.assertTrue(all(event.details["cancelled"] for event in cancelled_tools))
            self.assertFalse(cancelled_tools[0].details["not_started"])
            self.assertTrue(cancelled_tools[1].details["not_started"])

            records = read_trace_records(workspace / ".aster" / "trace.jsonl")
            self.assertIn("run.cancelled", [record["type"] for record in records])
            run_completed = next(record for record in records if record["type"] == "run.completed")
            self.assertEqual(run_completed["data"]["status"], "cancelled")
            tool_records = [record for record in records if record["type"] == "tool.call"]
            self.assertEqual([record["data"]["status"] for record in tool_records], ["cancelled", "cancelled"])
            self.assertEqual(run_completed["data"]["process"]["tool_errors"], 0)
            self.assertEqual(run_completed["data"]["process"]["tool_cancelled"], 2)
            dashboard = generate_dashboard(workspace, workspace / ".aster" / "dashboard")
            self.assertEqual(dashboard["cancelled_runs"], 1)
            self.assertEqual(dashboard["failure_rate"], 0)
            self.assertEqual(dashboard["tool_errors"], 0)
            self.assertEqual(dashboard["tool_cancelled"], 2)
            self.assertEqual(dashboard["failure_clusters"], [])

    async def test_cancelled_atomic_write_never_replaces_target_or_leaves_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / "target.txt"
            target.write_text("old", encoding="utf-8")
            staged = asyncio.Event()

            async def delayed_to_thread(function, *args, **kwargs):
                result = function(*args, **kwargs)
                staged.set()
                await asyncio.Event().wait()
                return result

            executor = ToolExecutor()
            executor.register(WriteTool(WorkspaceGuard(workspace)))
            token = CancellationToken()
            with patch("MiniClaw.coding_agent.tools.atomic.asyncio.to_thread", side_effect=delayed_to_thread):
                task = asyncio.create_task(
                    executor.execute(
                        ToolInvocation(
                            "write-1",
                            "write",
                            {"path": "target.txt", "content": "new"},
                        ),
                        token,
                    )
                )
                await asyncio.wait_for(staged.wait(), timeout=1)
                token.cancel("cancel before commit")
                with self.assertRaises(ToolCancelledError):
                    await asyncio.wait_for(task, timeout=1)

            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertEqual(list(workspace.glob(".*.miniclaw-*.tmp")), [])

    async def test_docker_cancellation_removes_container_and_reaps_cli_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            settings = RuntimeSettings(backend="docker", docker_image="python:3.11-slim")
            executor = _ObservedDockerExecutor(
                settings,
                WorkspaceGuard(workspace, "/workspace"),
                workspace / ".aster",
                "task",
            )
            process = _PendingProcess()
            spawned = asyncio.Event()

            async def fake_spawn(*_args, **_kwargs):
                spawned.set()
                return process

            async def fake_terminate(target) -> None:
                target.kill()

            token = CancellationToken()
            with (
                patch(
                    "MiniClaw.coding_agent.runtime.execution.asyncio.create_subprocess_exec",
                    side_effect=fake_spawn,
                ),
                patch(
                    "MiniClaw.coding_agent.runtime.execution.terminate_process_tree",
                    side_effect=fake_terminate,
                ),
            ):
                task = asyncio.create_task(executor.run("sleep 60", workspace, None, token))
                await asyncio.wait_for(spawned.wait(), timeout=1)
                token.cancel("cancel container")
                with self.assertRaises(ToolCancelledError):
                    await asyncio.wait_for(task, timeout=1)

            self.assertIsNotNone(process.returncode)
            self.assertTrue(executor.removed)
            self.assertTrue(executor.removed[0].startswith("miniclaw-"))


if __name__ == "__main__":
    unittest.main()
