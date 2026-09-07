from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from MiniClaw.agent.loop import AgentLoop
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.assistant.progress import TaskProgress
from MiniClaw.coding_agent.runtime import RuntimeSettings
from MiniClaw.coding_agent.tools.base import ToolResult
from MiniClaw.coding_agent.tools.executor import ToolExecutor
from MiniClaw.evaluation.recovery import recovered_run_ids, with_network_recovery
from MiniClaw.evaluation.runner import _aggregate_metrics
from MiniClaw.llm.openai_compatible import OpenAICompatibleClient
from MiniClaw.llm.types import AssistantReply, ModelProfile, ToolInvocation
from MiniClaw.trace.store import read_trace_records
from tests.test_agent_loop import ScriptedModelClient
from tests.test_llm_streaming import DelayedStream, request, sse, success_stream


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.progress = TaskProgress(Path(self.temp.name))
        self.progress.checkpoint("reconcile", [], "verify both fields", ["net_cents", "anomaly_order_ids"])

    def verify(self, checks, exit_code=0):
        self.progress.observe(ToolInvocation("v", "bash", {"task_verification": True}),
                              ToolResult(json.dumps({"task_checks": checks}), is_error=exit_code != 0,
                                         details={"exit_code": exit_code}))

    def checks(self):
        return [{"criterion": field, "passed": True, "evidence": "actual equals independently computed expected"}
                for field in ("net_cents", "anomaly_order_ids")]

    def test_correct_total_does_not_cover_missing_anomalies(self):
        self.verify(self.checks()[:1])
        self.assertEqual(self.progress.pending(), ["anomaly_order_ids"])
        self.assertIsNotNone(self.progress.final_blocker())

    def test_false_string_duplicate_and_failed_command_are_rejected(self):
        for kind in ("string", "duplicate", "exit", "false"):
            with self.subTest(kind=kind):
                checks = self.checks()
                if kind == "string":
                    checks[1]["passed"] = "false"
                if kind == "duplicate":
                    checks.append(checks[0])
                if kind == "false":
                    checks[1]["passed"] = False
                self.verify(checks, exit_code=1 if kind == "exit" else 0)
                self.assertIsNotNone(self.progress.final_blocker())

    def test_verified_checkpoint_survives_reload_and_mutation_invalidates_it(self):
        self.verify(self.checks())
        self.assertIsNone(TaskProgress(Path(self.temp.name)).final_blocker())
        for tool in ("write", "edit", "bash"):
            self.verify(self.checks())
            self.progress.observe(ToolInvocation("mutation", tool, {}), ToolResult("changed"))
            self.assertEqual(len(self.progress.pending()), 2)

    def test_unrelated_success_does_not_erase_failed_run(self):
        records = [
            {"type": "run.completed", "run_id": "a", "data": {"status": "error", "error": "ConnectError: offline"}},
            {"type": "run.completed", "run_id": "b", "data": {"status": "success"}},
        ]
        self.assertEqual(recovered_run_ids(records), set())
        metrics = _aggregate_metrics({"x": records})
        self.assertEqual(metrics["unrecovered_runs"], 1)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def assistant(self, model, directory):
        return CodingAssistant(model, ModelProfile("fake"), directory,
            runtime_settings=RuntimeSettings(backend="host"), environment={
                "MINICLAW_MEMORY_ENABLED": "false", "MINICLAW_MEMORY_CONSOLIDATION_ENABLED": "false",
                "MINICLAW_GOAL_JUDGE_ENABLED": "false", "MINICLAW_APPROVAL_POLICY": "allow",
            })

    async def test_budget_pause_persists_and_resumes_without_replaying_write(self):
        with tempfile.TemporaryDirectory() as directory:
            model = ScriptedModelClient([
                AssistantReply(tool_calls=[ToolInvocation("w", "write", {"path": "out.txt", "content": "OK"})]),
                AssistantReply(content="done"),
            ])
            assistant = self.assistant(model, directory)
            assistant.loop.max_turns = 1
            events = [event async for event in assistant.run("write out.txt")]
            self.assertEqual(events[-1].details["stop_reason"], "budget_exhausted")
            self.assertEqual(assistant.run_state_store.read().status, "paused")
            restored = self.assistant(model, directory)
            self.assertEqual(restored.task_progress.state["status"], "paused")
            resumed = [event async for event in restored.resume()]
            self.assertFalse(any(event.type == "tool_started" for event in resumed))
            self.assertEqual(Path(directory, "out.txt").read_text(), "OK")
            self.assertEqual(restored.run_state_store.read().status, "completed")

    async def test_unverified_completion_is_not_shown_as_success(self):
        model = ScriptedModelClient([AssistantReply(content="Everything is done")])
        loop = AgentLoop(model, ModelProfile("fake"), ToolExecutor(), max_turns=1,
                         final_guard=lambda: "missing anomaly check")
        events = [event async for event in loop.run("verify")]
        self.assertEqual(events[-1].details["stop_reason"], "budget_exhausted")
        self.assertFalse(any("Everything is done" in event.text for event in events))

    async def test_partial_stream_retries_same_request_and_discards_fragment(self):
        calls = []
        async def handler(wire):
            calls.append(wire.content)
            stream = (DelayedStream([(0, sse({"choices": [{"delta": {"content": "discard"}}]}))],
                                    httpx.ReadError("VPN switched")) if len(calls) == 1 else success_stream("done"))
            return httpx.Response(200, headers={"content-type": "text/event-stream", "x-request-id": f"p{len(calls)}"}, stream=stream)
        client = OpenAICompatibleClient("fake", "https://mock.test/v1", max_retries=1,
            retry_base_seconds=0.001, retry_max_seconds=0.001, retry_jitter_ratio=0,
            transport=httpx.MockTransport(handler))
        model_request = request()
        model_request.metadata["buffer_network_retries"] = True
        events = [event async for event in client.stream(model_request)]
        self.assertEqual(calls[0], calls[1])
        self.assertEqual("".join(event.text for event in events if event.type == "text_delta"), "done")
        self.assertTrue(any(event.details.get("provider_request_id") == "p2" for event in events))

    async def test_network_loss_after_write_recovers_without_duplicate_side_effects(self):
        calls = []
        async def handler(wire):
            calls.append(wire.content)
            if len(calls) == 1:
                stream = DelayedStream([(0, sse({"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "id": "w", "type": "function", "function": {
                        "name": "write", "arguments": json.dumps({"path": "out.txt", "content": "OK"})}
                }]}, "finish_reason": "tool_calls"}]})), (0, sse("[DONE]"))])
            elif len(calls) == 2:
                raise httpx.ConnectError("VPN switched")
            else:
                stream = success_stream("done")
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
        with tempfile.TemporaryDirectory() as directory:
            client = OpenAICompatibleClient("fake", "https://mock.test/v1", max_retries=0,
                                             transport=httpx.MockTransport(handler))
            assistant = self.assistant(client, directory)
            assistant.loop.max_turns = 3
            events = [event async for event in with_network_recovery(assistant, assistant.run("write out.txt"))]
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[1], calls[2])
            self.assertEqual(sum(event.type == "tool_started" for event in events), 1)
            records = read_trace_records(Path(directory, ".aster", "trace.jsonl"))
            metrics = _aggregate_metrics({"x": records})
            self.assertEqual(metrics["model_errors"], 1)
            self.assertEqual(metrics["network_model_errors"], 1)
            self.assertEqual(metrics["non_network_model_errors"], 0)
            self.assertEqual(metrics["network_recovered_runs"], 1)
            self.assertEqual(metrics["unrecovered_runs"], 0)
            self.assertEqual(assistant.run_state_store.read().turn, 2)

    async def test_exhausted_network_retries_stay_bounded(self):
        calls = 0
        async def handler(wire):
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("still offline")
        with tempfile.TemporaryDirectory() as directory:
            client = OpenAICompatibleClient("fake", "https://mock.test/v1", max_retries=0,
                                             transport=httpx.MockTransport(handler))
            assistant = self.assistant(client, directory)
            [event async for event in with_network_recovery(assistant, assistant.run("work"))]
            self.assertEqual(calls, 2)
            metrics = _aggregate_metrics({"x": read_trace_records(Path(directory, ".aster", "trace.jsonl"))})
            self.assertEqual(metrics["network_recovered_runs"], 0)
            self.assertGreater(metrics["unrecovered_runs"], 0)


if __name__ == "__main__":
    unittest.main()
