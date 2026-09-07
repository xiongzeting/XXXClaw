from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.goal.config import GoalConfig
from MiniClaw.llm.types import (
    AssistantReply,
    ChatMessage,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    TokenUsage,
    ToolInvocation,
)
from MiniClaw.coding_agent.memory.config import MemoryConfig
from MiniClaw.coding_agent.runtime import RuntimeSettings
from MiniClaw.trace.analysis import create_eval_case, generate_dashboard, migrate_legacy_traces
from MiniClaw.trace.replay import replay_trace
from MiniClaw.trace.store import TraceRecorder, read_trace_records
from MiniClaw.trace.model_client import TracingModelClient


class ScriptedModelClient:
    def __init__(self, replies: list[AssistantReply]) -> None:
        self.replies = replies
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        reply = self.replies.pop(0)
        yield ModelEvent(type="completed", reply=reply)


class TraceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_trace_records_mutually_exclusive_estimated_token_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inner = ScriptedModelClient(
                [AssistantReply(content="answer", usage=TokenUsage(input_tokens=42, output_tokens=7))]
            )
            recorder = TraceRecorder(directory, "test", "token-split")
            client = TracingModelClient(inner, recorder, provider="test")
            request = ModelRequest(
                ModelProfile("fake"),
                messages=[
                    # The marker takes precedence over role so this is memory evidence.
                    ChatMessage(
                        role="user", content="<retrieved_memory>historical fact</retrieved_memory>"
                    ),
                    ChatMessage(
                        role="system", content="[MiniClaw Working Context Checkpoint]\nprior summary"
                    ),
                    ChatMessage(
                        role="tool", name="read", content="tool output"
                    ),
                    ChatMessage(
                        role="user", content="current request"
                    ),
                ],
                tools=[{"name": "read", "description": "read a file"}],
            )
            _ = [event async for event in client.stream(request)]
            record = next(
                item for item in read_trace_records(Path(directory) / "trace.jsonl")
                if item["type"] == "model.request"
            )
            accounting = record["data"]["token_accounting"]
            estimated = accounting["estimated_input"]
            buckets = estimated["by_source"]
            self.assertTrue(estimated["is_estimate"])
            self.assertFalse(accounting["provider_usage"]["is_estimate"])
            self.assertEqual(estimated["total_tokens"], sum(buckets.values()))
            self.assertGreater(buckets["memory_injection"], 0)
            self.assertGreater(buckets["checkpoint_summary"], 0)
            self.assertGreater(buckets["tool_results"], 0)
            self.assertGreater(buckets["tool_definitions"], 0)
            self.assertEqual(record["data"]["purpose"], "agent")

    async def test_retrieved_memory_is_reference_evidence_not_system_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedModelClient([AssistantReply(content="done")])
            assistant = CodingAssistant(
                client,
                ModelProfile("fake"),
                directory,
                runtime_settings=RuntimeSettings(backend="host"),
            )
            sentinel = "MEMORY_SENTINEL historical parser diagnosis"
            assistant.memory.semantic.remember("project", sentinel)

            _ = [event async for event in assistant.run("MEMORY_SENTINEL parser")]

            request = client.requests[0]
            system = next(message.content for message in request.messages if message.role == "system")
            evidence = [
                message.content
                for message in request.messages
                if message.role == "assistant" and message.name == 'miniclaw_context'
            ]
            self.assertNotIn(sentinel, system)
            self.assertEqual(len(evidence), 1)
            self.assertIn(sentinel, evidence[0])
            self.assertIn("untrusted reference data", evidence[0].casefold())

    async def test_model_tool_tokens_cost_and_redaction_share_one_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = "sk-abcdefghijklmnop123456"
            client = ScriptedModelClient(
                [
                    AssistantReply(
                        tool_calls=[
                            ToolInvocation(
                                "write-1",
                                "write",
                                {"path": "result.txt", "content": secret},
                            )
                        ],
                        stop_reason="tool_calls",
                        usage=TokenUsage(input_tokens=1_000, output_tokens=100, cached_tokens=200),
                    ),
                    AssistantReply(
                        content="done",
                        usage=TokenUsage(input_tokens=500, output_tokens=50),
                    ),
                ]
            )
            profile = ModelProfile(
                "fake",
                input_cost_per_million=1,
                output_cost_per_million=2,
                cached_input_cost_per_million=0.5,
            )
            assistant = CodingAssistant(
                client,
                profile,
                directory,
                runtime_settings=RuntimeSettings(backend="host"),
                trace_provider="test",
            )
            events = [event async for event in assistant.run("write a file")]

            trace_path = Path(directory) / ".aster" / "trace.jsonl"
            records = read_trace_records(trace_path)
            types = [record["type"] for record in records]
            self.assertEqual(types.count("model.request"), 2)
            self.assertEqual(types.count("tool.call"), 1)
            self.assertIn("run.started", types)
            self.assertIn("run.completed", types)
            self.assertIn("goal.snapshot", types)
            started = next(record for record in records if record["type"] == "run.started")
            self.assertEqual(started["data"]["runtime"]["backend"], "host")
            self.assertEqual(started["data"]["runtime"]["workspace_mode"], "direct")
            completed = next(record for record in records if record["type"] == "run.completed")
            self.assertAlmostEqual(completed["data"]["usage"]["cost_usd"], 0.0017)
            self.assertEqual(events[-1].details["run_id"], completed["run_id"])
            self.assertNotIn(secret, trace_path.read_text(encoding="utf-8"))
            self.assertIn("[REDACTED_TOKEN]", trace_path.read_text(encoding="utf-8"))

    async def test_model_based_compaction_is_traced_with_its_own_purpose_and_savings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedModelClient(
                [
                    AssistantReply(
                        content="attempt finished",
                        usage=TokenUsage(input_tokens=1_000, output_tokens=20),
                    ),
                    AssistantReply(
                        content="compact checkpoint",
                        usage=TokenUsage(input_tokens=300, output_tokens=30),
                    ),
                ]
            )
            assistant = CodingAssistant(
                client,
                ModelProfile("fake"),
                directory,
                runtime_settings=RuntimeSettings(backend="host"),
            )
            compact_config = MemoryConfig(
                enabled=True,
                reserve_tokens=100,
                keep_recent_tokens=1,
                soft_trigger_tokens=50,
                hard_trigger_tokens=100,
                target_tokens=40,
                progressive_enabled=True,
                artifact_threshold_bytes=16_384,
                artifact_preview_chars=1_000,
                deterministic_semantic_tokens=1,
            )
            assistant.memory.config = compact_config
            assistant.memory.working.config = compact_config
            _ = [event async for event in assistant.run("x" * 2_000)]

            records = read_trace_records(Path(directory) / ".aster" / "trace.jsonl")
            purposes = [
                record["data"]["purpose"]
                for record in records
                if record["type"] == "model.request"
            ]
            self.assertEqual(purposes, ["agent", "compaction"])
            self.assertIn("compaction.started", [record["type"] for record in records])
            completed = next(record for record in records if record["type"] == "compaction.completed")
            self.assertEqual(completed["data"]["strategy"], "model-summary")
            self.assertGreater(completed["data"]["tokens_saved"], 0)

    async def test_goal_attempt_terminal_state_is_recorded_after_outer_supervision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedModelClient([AssistantReply(content="partial")])
            assistant = CodingAssistant(
                client,
                ModelProfile("fake"),
                directory,
                runtime_settings=RuntimeSettings(backend="host"),
                goal_config=GoalConfig(max_attempts=1),
            )
            assistant.create_goal("goal", ["criterion"])
            _ = [event async for event in assistant.run_goal("start")]
            records = read_trace_records(Path(directory) / ".aster" / "trace.jsonl")
            snapshots = [record for record in records if record["type"] == "goal.snapshot"]
            self.assertEqual(snapshots[-1]["data"]["phase"], "attempt_finished")
            self.assertEqual(snapshots[-1]["data"]["goal"]["status"], "failed")

    async def test_boundary_replay_switches_prompt_model_and_tool_config_without_running_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "session"
            recorder = TraceRecorder(session, "test", "conversation")
            run_id = recorder.new_run_id()
            recorder.record(
                "run.started",
                {"request": "task", "provider": "old", "model": "old", "tools": ["read", "write"]},
                run_id=run_id,
            )
            recorder.record(
                "model.request",
                {
                    "request_id": "request-1",
                    "purpose": "agent",
                    "status": "success",
                    "stop_reason": "tool_calls",
                    "usage": {"total_tokens": 10, "cost_usd": 0},
                    "context": {
                        "messages": [
                            {"role": "system", "content": "old prompt", "tool_calls": []},
                            {"role": "user", "content": "task", "tool_calls": []},
                        ],
                        "tools": [
                            {"name": "read", "description": "read", "parameters": {"type": "object"}},
                            {"name": "write", "description": "write", "parameters": {"type": "object"}},
                        ],
                    },
                    "output": {
                        "content": "",
                        "tool_calls": [{"call_id": "old", "name": "write", "arguments": {}}],
                    },
                },
                run_id=run_id,
            )
            recorder.record(
                "run.completed",
                {"status": "error", "stop_reason": "error", "error": "old failure", "usage": {}},
                run_id=run_id,
            )
            prompt = Path(directory) / "prompt.txt"
            prompt.write_text("new prompt", encoding="utf-8")
            tools = Path(directory) / "tools.json"
            tools.write_text(
                json.dumps(
                    {
                        "enabled": ["write"],
                        "overrides": {"write": {"description": "new write description"}},
                    }
                ),
                encoding="utf-8",
            )
            eval_root = Path(directory) / "eval-suite"
            case_path = eval_root / "eval-case.json"
            create_eval_case(session, case_path, run_id)
            replay_client = ScriptedModelClient(
                [
                    AssistantReply(
                        tool_calls=[ToolInvocation("new", "write", {})],
                        stop_reason="tool_calls",
                    )
                ]
            )
            report = await replay_trace(
                session,
                environment={"MINICLAW_PRIMARY_API_KEY": "dummy"},
                output_path=eval_root / "replay.json",
                run_id=run_id,
                model_id="new-model",
                prompt_path=prompt,
                tool_config_path=tools,
                eval_case_path=case_path,
                model_client=replay_client,
            )
            request = replay_client.requests[0]
            self.assertEqual(request.profile.model_id, "new-model")
            self.assertEqual(request.messages[0].content, "new prompt")
            self.assertEqual([tool["name"] for tool in request.tools], ["write"])
            self.assertEqual(request.tools[0]["description"], "new write description")
            self.assertTrue(report["boundaries"][0]["tool_call_sequence_match"])
            self.assertTrue((eval_root / "eval-results.jsonl").exists())

    async def test_benchmark_answer_failure_becomes_deterministic_eval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "benchmark"
            recorder = TraceRecorder(session, "benchmark", "memory")
            run_id = recorder.new_run_id()
            recorder.record(
                "benchmark.case.started",
                {"benchmark": "MemoryAgentBench", "case_id": "case-1"},
                run_id=run_id,
            )
            recorder.record(
                "model.request",
                {
                    "request_id": "answer-1",
                    "purpose": "agent",
                    "status": "success",
                    "provider": "old",
                    "model": "old",
                    "stop_reason": "stop",
                    "context": {
                        "messages": [{"role": "user", "content": "continent?", "tool_calls": []}],
                        "tools": [],
                    },
                    "output": {"content": "FINAL: Africa", "tool_calls": []},
                },
                run_id=run_id,
            )
            recorder.record(
                "benchmark.case.completed",
                {
                    "case_id": "case-1",
                    "source": "factconsolidation_mh_64k",
                    "question": "continent?",
                    "answers": ["North America"],
                    "output": "FINAL: Africa",
                    "substring_exact_match": False,
                    "metrics": {"usage": {"total_tokens": 10, "cost_usd": 0.001}},
                },
                run_id=run_id,
            )
            eval_root = Path(directory) / "eval-suite"
            case_path = eval_root / "eval-case.json"
            case = create_eval_case(session, case_path)
            self.assertEqual(case["source"]["type"], "benchmark_failure")
            self.assertEqual(case["judge"]["type"], "normalized_contains_any")
            self.assertEqual(case["failure"]["signature"], "benchmark:factconsolidation_mh_64k:answer_mismatch")

            report = await replay_trace(
                session,
                environment={"MINICLAW_PRIMARY_API_KEY": "dummy"},
                output_path=eval_root / "replay.json",
                model_id="new-model",
                eval_case_path=case_path,
                model_client=ScriptedModelClient([AssistantReply(content="FINAL: North America")]),
            )
            self.assertTrue(report["evaluation"]["passed"])
            self.assertEqual(report["evaluation"]["confidence"], "deterministic")
            self.assertEqual(report["evaluation"]["judge"]["output_preview"], "FINAL: North America")
            dashboard = generate_dashboard(eval_root, eval_root / "dashboard")
            self.assertEqual(dashboard["runs"], 1)
            self.assertEqual(dashboard["successful_runs"], 0)
            self.assertEqual(
                dashboard["failure_clusters"][0]["signature"],
                "benchmark:factconsolidation_mh_64k:answer_mismatch",
            )

    async def test_long_memory_judge_failure_replays_with_rubric(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "benchmark"
            recorder = TraceRecorder(session, "benchmark", "long-memory")
            run_id = recorder.new_run_id()
            recorder.record(
                "benchmark.case.started",
                {"benchmark": "LongMemEval", "question_id": "preference-1"},
                run_id=run_id,
            )
            recorder.record(
                "model.request",
                {
                    "request_id": "answer-1",
                    "purpose": "agent",
                    "status": "success",
                    "provider": "old",
                    "model": "old",
                    "stop_reason": "stop",
                    "context": {
                        "messages": [{"role": "user", "content": "recommend a show", "tool_calls": []}],
                        "tools": [],
                    },
                    "output": {"content": "Watch an action film", "tool_calls": []},
                },
                run_id=run_id,
            )
            recorder.record(
                "model.request",
                {
                    "request_id": "judge-1",
                    "purpose": "longmemeval_judge",
                    "status": "success",
                    "provider": "old",
                    "model": "old",
                    "stop_reason": "stop",
                    "context": {"messages": [], "tools": []},
                    "output": {"content": "no", "tool_calls": []},
                },
                run_id=run_id,
            )
            recorder.record(
                "benchmark.case.completed",
                {
                    "question_id": "preference-1",
                    "category": "single-session-preference",
                    "question": "recommend a show",
                    "answer": "Recommend a storytelling stand-up special on Netflix.",
                    "output": "Watch an action film",
                    "judge": {"label": False, "response": "no"},
                },
                run_id=run_id,
            )
            case_path = Path(directory) / "case.json"
            case = create_eval_case(session, case_path)
            self.assertEqual(len(case["replay_boundaries"]), 1)
            self.assertEqual(case["judge"]["type"], "llm_rubric")
            replay_client = ScriptedModelClient(
                [
                    AssistantReply(content="Watch a storytelling stand-up special on Netflix."),
                    AssistantReply(content="yes"),
                ]
            )
            report = await replay_trace(
                session,
                environment={"MINICLAW_PRIMARY_API_KEY": "dummy"},
                output_path=Path(directory) / "replay.json",
                model_id="new-model",
                eval_case_path=case_path,
                model_client=replay_client,
            )
            self.assertTrue(report["evaluation"]["passed"])
            self.assertEqual(report["evaluation"]["confidence"], "llm-judge")
            self.assertEqual(
                report["evaluation"]["judge"]["output_preview"],
                "Watch a storytelling stand-up special on Netflix.",
            )
            self.assertEqual(len(replay_client.requests), 2)

    async def test_longmemeval_v2_phrase_judge_accepts_boxed_latex_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "benchmark"
            recorder = TraceRecorder(session, "benchmark", "long-memory-v2")
            run_id = recorder.new_run_id()
            recorder.record(
                "benchmark.case.started",
                {"benchmark": "LongMemEval-V2-Small", "question_id": "phrase-1"},
                run_id=run_id,
            )
            recorder.record(
                "model.request",
                {
                    "request_id": "answer-1",
                    "purpose": "longmemeval_v2_answer",
                    "status": "success",
                    "provider": "old",
                    "model": "old",
                    "stop_reason": "stop",
                    "context": {
                        "messages": [{"role": "user", "content": "metric columns?", "tool_calls": []}],
                        "tools": [],
                    },
                    "output": {"content": r"\boxed{wrong}", "tool_calls": []},
                },
                run_id=run_id,
            )
            recorder.record(
                "benchmark.case.completed",
                {
                    "question_id": "phrase-1",
                    "question_type": "static-environment",
                    "question": "metric columns?",
                    "answer_gold": "Results, Uses",
                    "eval_function": "norm_phrase_set_match|separators=,;|require_non_empty=true",
                    "response_raw": r"\boxed{wrong}",
                    "correct": False,
                    "usage": {
                        "input_tokens": 90,
                        "output_tokens": 10,
                        "cached_tokens": 0,
                        "total_tokens": 100,
                        "cost_usd": 0.01,
                    },
                    "elapsed_seconds": 1.25,
                },
                run_id=run_id,
            )
            case_path = Path(directory) / "eval-case.json"
            case = create_eval_case(session, case_path)
            self.assertEqual(case["judge"]["type"], "normalized_phrase_set")
            self.assertEqual(case["judge"]["expected"], ["Results", "Uses"])

            report = await replay_trace(
                session,
                environment={"MINICLAW_PRIMARY_API_KEY": "dummy"},
                output_path=Path(directory) / "replay.json",
                model_id="new-model",
                eval_case_path=case_path,
                model_client=ScriptedModelClient(
                    [AssistantReply(content=r"\(\boxed{\text{Results, Uses}}\)")]
                ),
            )

            self.assertTrue(report["evaluation"]["passed"])
            self.assertEqual(report["evaluation"]["confidence"], "deterministic")
            dashboard = generate_dashboard(Path(directory), Path(directory) / "dashboard")
            self.assertEqual(dashboard["total_tokens"], 100)
            self.assertEqual(dashboard["total_cost_usd"], 0.01)
            self.assertEqual(dashboard["average_duration_ms"], 1250)
            self.assertEqual(
                dashboard["failure_clusters"][0]["signature"],
                "benchmark:static-environment:answer_mismatch",
            )
            self.assertIn("Results, Uses", dashboard["failure_clusters"][0]["examples"][0])

    def test_longmemeval_v2_checker_request_is_not_replayed_as_agent_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "benchmark"
            recorder = TraceRecorder(session, "benchmark", "long-memory-v2")
            run_id = recorder.new_run_id()
            recorder.record(
                "benchmark.case.started",
                {"benchmark": "LongMemEval-V2-Small", "question_id": "abstention-1"},
                run_id=run_id,
            )
            for request_id, purpose, content in (
                ("answer-1", "longmemeval_v2_answer", r"\boxed{UNKNOWN}"),
                (
                    "checker-1",
                    "longmemeval_v2_llm_abstention_checker",
                    '{"label":0,"reason":"unknown is insufficient"}',
                ),
            ):
                recorder.record(
                    "model.request",
                    {
                        "request_id": request_id,
                        "purpose": purpose,
                        "status": "success",
                        "provider": "old",
                        "model": "old",
                        "stop_reason": "stop",
                        "context": {
                            "messages": [{"role": "user", "content": "question", "tool_calls": []}],
                            "tools": [],
                        },
                        "output": {"content": content, "tool_calls": []},
                    },
                    run_id=run_id,
                )
            recorder.record(
                "benchmark.case.completed",
                {
                    "question_id": "abstention-1",
                    "question_type": "static-environment-abs",
                    "question": "Does the nonexistent control appear?",
                    "answer_gold": "The control does not appear.",
                    "eval_function": "llm_abstention_checker|require_non_empty=true",
                    "response_raw": r"\boxed{UNKNOWN}",
                    "correct": False,
                },
                run_id=run_id,
            )

            case = create_eval_case(session)

            self.assertEqual(len(case["replay_boundaries"]), 1)
            self.assertEqual(case["replay_boundaries"][0]["request_id"], "answer-1")


class TraceAnalysisTests(unittest.TestCase):
    def test_real_failure_becomes_eval_case_and_dashboard_cluster(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "sessions" / "one"
            recorder = TraceRecorder(session, "feishu", "one")
            run_id = recorder.new_run_id()
            recorder.record(
                "run.started",
                {
                    "request": "修复测试",
                    "provider": "test",
                    "model": "fake",
                    "tools": ["bash"],
                    "goal": {"goal": "修复测试", "acceptance_criteria": ["测试通过"]},
                },
                run_id=run_id,
            )
            recorder.record(
                "tool.call",
                {
                    "tool_name": "bash",
                    "status": "error",
                    "error": "[NON_ZERO_EXIT] pytest failed 2 tests",
                },
                run_id=run_id,
            )
            recorder.record(
                "run.completed",
                {
                    "status": "error",
                    "stop_reason": "error",
                    "error": "[NON_ZERO_EXIT] pytest failed 2 tests",
                    "duration_ms": 100,
                    "usage": {"total_tokens": 100, "cost_usd": 0.01},
                    "goal": {"goal": "修复测试", "status": "failed", "created_at": "x", "updated_at": "y"},
                },
                run_id=run_id,
            )
            case = create_eval_case(session, root / "case.json")
            self.assertEqual(case["failure"]["signature"], "tool:bash:NON_ZERO_EXIT")
            baseline = root / "baseline.json"
            baseline.write_text(
                json.dumps(
                    {
                        "failure_rate": 0,
                        "eval_attempts": 0,
                        "duration_p95_ms": 50,
                        "average_cost_usd": 0.001,
                        "tool_error_rate": 0,
                        "goal_runs": 1,
                        "goal_completion_rate": 1,
                    }
                ),
                encoding="utf-8",
            )
            summary = generate_dashboard(root, root / "dashboard", baseline)
            self.assertEqual(summary["failure_clusters"][0]["signature"], "tool:bash:NON_ZERO_EXIT")
            self.assertGreaterEqual(len(summary["regression_alerts"]), 4)
            self.assertTrue((root / "dashboard" / "dashboard.html").exists())

    def test_legacy_tool_calls_and_model_metrics_migrate_to_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory) / "legacy"
            session.mkdir()
            (session / "tool-calls.jsonl").write_text(
                json.dumps(
                    {
                        "recordType": "tool-call",
                        "channelId": "old",
                        "conversationId": "legacy",
                        "toolCallId": "call-1",
                        "toolName": "bash",
                        "status": "error",
                        "args": {"command": "pytest"},
                        "error": "failed",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (session / "model-requests.jsonl").write_text(
                json.dumps(
                    {
                        "requestId": "request-1",
                        "channelId": "old",
                        "conversationId": "legacy",
                        "provider": "deepseek",
                        "model": "old-model",
                        "durationMs": 10,
                        "stopReason": "error",
                        "error": "failed",
                        "tokens": {"input": 10, "output": 2, "total": 12},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = migrate_legacy_traces(directory)
            self.assertEqual(result["migrated"], 1)
            records = read_trace_records(session / "trace.jsonl")
            self.assertIn("tool.call", [record["type"] for record in records])
            self.assertIn("model.request", [record["type"] for record in records])


if __name__ == "__main__":
    unittest.main()
