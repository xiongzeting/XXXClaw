from __future__ import annotations

import asyncio
import unittest

from MiniClaw.cancellation import CancellationToken, ToolCancelledError
from MiniClaw.coding_agent.tools import ToolContext, ToolExecutor, ToolResult
from MiniClaw.llm.types import ToolInvocation


class ToolExecutorP0Tests(unittest.IsolatedAsyncioTestCase):
    class Echo:
        name = "echo"
        description = "echo"
        input_schema = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}

        async def execute(self, arguments, cancellation_token=None):
            return ToolResult(arguments["value"])

    async def test_success_records_complete_lifecycle_and_delivery_metadata(self) -> None:
        executor = ToolExecutor(tools=[self.Echo()])

        result = await executor.execute(ToolInvocation("call-1", "echo", {"value": "ok"}))

        self.assertFalse(result.is_error)
        self.assertEqual(result.details["lifecycle"], "succeeded")
        self.assertEqual(result.details["lifecycle_phase"], "delivered")
        self.assertFalse(result.details["not_started"])
        phases = [event["phase"] for event in result.details["trace"]["events"]]
        self.assertEqual(phases[0], "received")
        self.assertIn("validated", phases)
        self.assertIn("preflighted", phases)
        self.assertIn("started", phases)
        self.assertIn("succeeded", phases)
        self.assertIn("transformed", phases)
        self.assertEqual(phases[-1], "delivered")
        self.assertTrue(all("started_at" in event and "ended_at" in event for event in result.details["trace"]["events"]))
        self.assertEqual(executor.call_history[-1]["call_id"], "call-1")
        self.assertEqual(executor.call_history[-1]["outcome"], "succeeded")

    async def test_unknown_tool_is_returned_once_without_runtime_retry(self) -> None:
        executor = ToolExecutor(max_retries=5, retry_base_seconds=0)

        result = await executor.execute(ToolInvocation("unknown-1", "missing", {}))

        self.assertTrue(result.is_error)
        self.assertEqual(result.details["error"]["code"], "UNKNOWN_TOOL")
        self.assertTrue(result.details["not_started"])
        self.assertEqual(len(executor.call_history), 1)
        self.assertEqual(len(executor.call_history[0]["attempts"]), 0)

    async def test_network_error_retries_at_most_five_times_with_backoff_metadata(self) -> None:
        class Offline:
            name = "read"
            description = "read"
            input_schema = {"type": "object"}

            def __init__(self):
                self.calls = 0

            async def execute(self, arguments, cancellation_token=None):
                self.calls += 1
                raise ConnectionError("connection reset by peer")

        tool = Offline()
        executor = ToolExecutor(
            tools=[tool], max_retries=50, retry_base_seconds=0, retry_max_seconds=0
        )

        result = await executor.execute(ToolInvocation("network-1", "read", {}))

        self.assertTrue(result.is_error)
        self.assertEqual(tool.calls, 6)  # initial attempt + five retries
        self.assertEqual(result.details["retry_count"], 5)
        history = executor.call_history[-1]
        self.assertEqual(len(history["attempts"]), 6)
        self.assertEqual(len([event for event in history["events"] if event["phase"] == "retry_scheduled"]), 5)
        self.assertEqual(history["error"]["code"], "NETWORK_ERROR")

    async def test_non_network_error_is_returned_to_model_without_runtime_loop(self) -> None:
        class Broken:
            name = "broken"
            description = "broken"
            input_schema = {"type": "object"}

            def __init__(self):
                self.calls = 0

            async def execute(self, arguments, cancellation_token=None):
                self.calls += 1
                raise ValueError("fix the argument")

        tool = Broken()
        executor = ToolExecutor(tools=[tool], max_retries=5, retry_base_seconds=0)

        result = await executor.execute(ToolInvocation("broken-1", "broken", {}))

        self.assertTrue(result.is_error)
        self.assertEqual(tool.calls, 1)
        self.assertEqual(result.details["error"]["code"], "TOOL_EXECUTION_FAILED")
        self.assertFalse(result.details["error"]["retryable"])

    async def test_preflight_approval_is_structured_as_policy_error(self) -> None:
        executor = ToolExecutor(
            tools=[self.Echo()],
            preflights=[lambda call: ToolResult(
                "denied",
                is_error=True,
                details={"approval": {"decision": "denied-by-policy"}},
            )]
        )
        result = await executor.execute(ToolInvocation("approval-1", "echo", {"value": "x"}))

        self.assertTrue(result.is_error)
        self.assertEqual(result.details["error"]["code"], "POLICY_DENIED")
        self.assertTrue(result.details["not_started"])
        self.assertEqual(result.details["trace"]["events"][2]["phase"], "preflighted")

    async def test_cancellation_is_recorded_before_compatibility_reraise(self) -> None:
        class Slow:
            name = "slow"
            description = "slow"
            input_schema = {"type": "object"}

            async def execute(self, arguments, cancellation_token=None):
                await asyncio.sleep(10)
                return ToolResult("done")

        token = CancellationToken()
        executor = ToolExecutor(tools=[Slow()])
        task = asyncio.create_task(executor.execute(ToolInvocation("cancel-1", "slow", {}), token))
        await asyncio.sleep(0)
        token.cancel("user cancelled")

        with self.assertRaises(ToolCancelledError):
            await task
        history = executor.call_history[-1]
        self.assertEqual(history["outcome"], "cancelled")
        cancelled = [event for event in history["events"] if event["phase"] == "cancelled"][-1]
        self.assertEqual(cancelled["error"]["code"], "CANCELLED")
        self.assertEqual(history["events"][-1]["phase"], "delivered")

    async def test_context_is_injected_and_output_truncation_is_traceable(self) -> None:
        class ContextTool:
            name = "context"
            description = "context"
            input_schema = {"type": "object"}

            async def execute(self, arguments, context: ToolContext | None = None):
                return ToolResult(f"{context.session_id}:" + "x" * 20)

        from MiniClaw.coding_agent.tools.base import ToolContext as Context

        executor = ToolExecutor(
            tools=[ContextTool()],
            max_output_chars=8,
            context=Context(session_id="session-1"),
        )
        result = await executor.execute(ToolInvocation("context-1", "context", {}))

        self.assertTrue(result.content.startswith("session-"))
        self.assertTrue(result.details["truncated"])
        self.assertEqual(result.details["error"]["code"], "OUTPUT_TRUNCATED")
        self.assertTrue(result.details["trace"]["truncated"])

    async def test_successful_verification_command_is_reused_until_a_mutation(self) -> None:
        class Verify:
            name = "bash"
            description = "bash"
            input_schema = {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            }

            def __init__(self):
                self.calls = 0

            async def execute(self, arguments, cancellation_token=None):
                self.calls += 1
                return ToolResult("VERIFIED")

        tool = Verify()
        executor = ToolExecutor(tools=[tool])
        first = await executor.execute(ToolInvocation("verify-1", "bash", {"command": "pytest -q"}))
        second = await executor.execute(ToolInvocation("verify-2", "bash", {"command": "pytest -q"}))

        self.assertFalse(first.is_error)
        self.assertFalse(second.is_error)
        self.assertEqual(tool.calls, 1)
        self.assertTrue(second.details["cached_reuse"])


if __name__ == "__main__":
    unittest.main()
