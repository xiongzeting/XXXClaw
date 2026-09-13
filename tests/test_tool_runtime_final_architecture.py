from __future__ import annotations

import importlib
import unittest
import tempfile
from pathlib import Path

from MiniClaw.cancellation import CancellationToken, ToolCancelledError
from MiniClaw.coding_agent.tools import ToolExecutor, ToolResult
from MiniClaw.coding_agent.tools.base import ToolSpec
from MiniClaw.coding_agent.runtime import RuntimeSettings, create_tool_runtime
from MiniClaw.llm.types import ToolInvocation


class ToolRuntimeFinalArchitectureTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_executor_is_the_only_tool_boundary(self) -> None:
        import MiniClaw.coding_agent.tools as tool_module

        self.assertFalse(hasattr(ToolExecutor, "register"))
        self.assertFalse(hasattr(ToolExecutor, "inject"))
        self.assertFalse(hasattr(ToolExecutor, "set_role"))
        self.assertFalse(hasattr(tool_module, "ToolManager"))
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("MiniClaw.coding_agent.tools.manager")

    async def test_preflight_runs_once_while_idempotent_network_call_retries(self) -> None:
        class FlakyRead:
            name = "read"
            description = "read"
            input_schema = {"type": "object", "additionalProperties": False}

            def __init__(self) -> None:
                self.calls = 0

            async def execute(self, arguments, cancellation_token=None):
                self.calls += 1
                if self.calls == 1:
                    raise ConnectionError("temporary connection reset")
                return ToolResult("ok")

        calls = 0

        def preflight(_call):
            nonlocal calls
            calls += 1
            return None

        tool = FlakyRead()
        executor = ToolExecutor(
            tools=[tool],
            preflights=[preflight],
            max_retries=1,
            retry_base_seconds=0,
            retry_max_seconds=0,
        )
        result = await executor.execute(ToolInvocation("read-1", "read", {}))

        self.assertEqual(result.content, "ok")
        self.assertEqual(calls, 1)
        self.assertEqual(tool.calls, 2)
        phases = [item["phase"] for item in result.details["trace"]["events"]]
        self.assertEqual(phases.count("transformed"), 1)
        self.assertEqual(phases.count("delivered"), 1)

    async def test_non_idempotent_network_failure_is_not_replayed(self) -> None:
        class MutatingTool:
            name = "mutate"
            description = "mutate"
            input_schema = {"type": "object", "additionalProperties": False}
            tool_spec = ToolSpec(name="mutate", side_effect="irreversible")

            def __init__(self) -> None:
                self.calls = 0

            async def execute(self, arguments, cancellation_token=None):
                self.calls += 1
                raise ConnectionError("connection lost after commit")

        tool = MutatingTool()
        executor = ToolExecutor(tools=[tool], max_retries=5, retry_base_seconds=0)

        result = await executor.execute(ToolInvocation("mutate-1", "mutate", {}))

        self.assertTrue(result.is_error)
        self.assertEqual(tool.calls, 1)
        self.assertEqual(result.details["retry_count"], 0)
        self.assertTrue(result.details["uncertain_side_effect"])
        self.assertTrue(result.details["error"]["uncertain_side_effect"])

    async def test_request_advertisement_and_execution_share_one_boundary(self) -> None:
        class NamedTool:
            input_schema = {"type": "object", "additionalProperties": False}
            description = "test"

            def __init__(self, name: str) -> None:
                self.name = name

            async def execute(self, arguments):
                return ToolResult(self.name)

        executor = ToolExecutor(tools=[NamedTool("read"), NamedTool("write")])

        executor.set_request_tool_names(["read"])
        self.assertEqual(executor.available_names(), ("read",))
        definitions = executor.definitions()
        self.assertEqual([item["name"] for item in definitions], ["read"])
        self.assertNotIn("side_effect", definitions[0])
        self.assertNotIn("retry_policy", definitions[0])
        result = await executor.execute(ToolInvocation("write-1", "write", {}))
        self.assertTrue(result.is_error)
        self.assertTrue(result.details["not_started"])
        self.assertEqual(result.details["error"]["code"], "TOOL_UNAVAILABLE")

    async def test_cancellation_before_execution_is_structured_and_does_not_start_tool(self) -> None:
        class NeverRun:
            name = "read"
            description = "read"
            input_schema = {"type": "object", "additionalProperties": False}

            async def execute(self, arguments, cancellation_token=None):
                raise AssertionError("tool should not start")

        token = CancellationToken()
        token.cancel("stopped before dispatch")
        executor = ToolExecutor(tools=[NeverRun()])

        with self.assertRaises(ToolCancelledError):
            await executor.execute(ToolInvocation("cancel-1", "read", {}), token)
        history = executor.call_history[-1]
        self.assertFalse(history["started"])
        self.assertEqual(history["status"], "cancelled")

    async def test_runtime_validates_one_workspace_root_and_one_timeout_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = create_tool_runtime(
                root,
                root / ".session",
                "runtime-boundary",
                RuntimeSettings(
                    backend="host",
                    default_command_timeout_seconds=2,
                    max_command_timeout_seconds=3,
                ),
            )
            with self.assertRaises(PermissionError):
                await runtime.run("echo should-not-run", root.parent, 1)
            with self.assertRaises(ValueError):
                await runtime.run("echo should-not-run", root, 4)


if __name__ == "__main__":
    unittest.main()
