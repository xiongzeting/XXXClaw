from __future__ import annotations

import tempfile
import unittest
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

from MiniClaw.agent.events import AgentEvent
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.goal.config import GoalConfig, load_goal_config, load_goal_judge_config
from MiniClaw.coding_agent.goal.prompts import parse_goal_command
from MiniClaw.coding_agent.goal.judge import GoalJudge
from MiniClaw.coding_agent.goal.state import GoalCriterionEvidence
from MiniClaw.coding_agent.goal.store import GoalStore
from MiniClaw.coding_agent.goal.supervisor import GoalSupervisor
from MiniClaw.coding_agent.runtime import RuntimeSettings
from MiniClaw.llm.types import (
    AssistantReply,
    ChatMessage,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    ToolInvocation,
)


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=1)
        return current


class ScriptedModelClient:
    def __init__(self, replies: list[AssistantReply]) -> None:
        self.replies = replies
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        reply = self.replies.pop(0)
        yield ModelEvent(type="completed", reply=reply)


class GoalStoreTests(unittest.TestCase):
    def test_goal_persists_and_requires_fresh_successful_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(directory, now=MutableClock())
            created = store.create("完成 Goal", ["测试通过", "文件已写入"], "user")
            self.assertEqual(created.status, "active")
            self.assertEqual(GoalStore(directory).read().goal, "完成 Goal")  # type: ignore[union-attr]

            with self.assertRaisesRegex(ValueError, "GOAL_NOT_VERIFYING"):
                store.validate_completion(
                    "done",
                    [
                        GoalCriterionEvidence("测试通过", "pytest"),
                        GoalCriterionEvidence("文件已写入", "result.txt"),
                    ],
                )

            store.start_verification("v1", "python -m unittest")
            store.finish_verification("v1", passed=True, exit_code=0, output="OK")
            with self.assertRaisesRegex(ValueError, "GOAL_CRITERION_UNVERIFIED"):
                store.complete("done", [GoalCriterionEvidence("测试通过", "OK")])

            completed = store.complete(
                "done",
                [
                    GoalCriterionEvidence("测试通过", "verification output: OK"),
                    GoalCriterionEvidence("文件已写入", "verified by the test command"),
                ],
            )
            self.assertEqual(completed.status, "complete")
            self.assertEqual(completed.final_result, "done")

    def test_file_mutation_invalidates_old_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(directory, now=MutableClock())
            store.create("目标", ["条件"])
            store.start_verification("v1", "pytest")
            store.finish_verification("v1", passed=True, exit_code=0, output="passed")
            state = store.invalidate_verification("write modified app.py")
            self.assertEqual(state.status, "active")  # type: ignore[union-attr]
            self.assertEqual(state.verification_history, [])  # type: ignore[union-attr]
            with self.assertRaisesRegex(ValueError, "GOAL_NOT_VERIFYING"):
                store.complete("done", [GoalCriterionEvidence("条件", "old test")])

    def test_attempt_limit_fails_goal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(
                directory,
                config=GoalConfig(max_attempts=1, max_duration_seconds=60, max_cost_usd=10),
                now=MutableClock(),
            )
            store.create("目标", ["条件"])
            self.assertTrue(store.begin_attempt().allowed)
            failed = store.finish_attempt(0, "partial")
            self.assertEqual(failed.status, "failed")
            self.assertIn("attempt limit", failed.final_result or "")

    def test_wait_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(directory, now=MutableClock())
            store.create("目标", ["条件"])
            self.assertEqual(store.wait_for_user("需要选择").status, "waiting_for_user")
            self.assertEqual(store.resume().status, "active")


class GoalSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_natural_stop_is_an_attempt_and_supervisor_continues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(directory, now=MutableClock())
            store.create("目标", ["条件"])
            prompts: list[str] = []

            async def run_once(prompt: str) -> AsyncIterator[AgentEvent]:
                prompts.append(prompt)
                if len(prompts) == 2:
                    store.start_verification("v2", "pytest")
                    store.finish_verification("v2", passed=True, exit_code=0, output="1 passed")
                    store.complete("完成", [GoalCriterionEvidence("条件", "pytest: 1 passed")])
                message = ChatMessage(role="assistant", content=f"attempt {len(prompts)}")
                yield AgentEvent(
                    type="run_finished",
                    message=message,
                    details={"stop_reason": "stop"},
                )

            events = [event async for event in GoalSupervisor(store, run_once).run("开始")]
            self.assertEqual(len(events), 2)
            self.assertEqual(len(prompts), 2)
            self.assertIn("Goal execution contract", prompts[0])
            self.assertIn("Continue working autonomously", prompts[1])
            self.assertEqual(store.read().status, "complete")  # type: ignore[union-attr]
            self.assertEqual(store.read().attempt_count, 2)  # type: ignore[union-attr]

    async def test_error_does_not_retry_forever(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(directory, now=MutableClock())
            store.create("目标", ["条件"])
            calls = 0

            async def run_once(prompt: str) -> AsyncIterator[AgentEvent]:
                nonlocal calls
                calls += 1
                yield AgentEvent(
                    type="run_finished",
                    is_error=True,
                    details={"stop_reason": "error"},
                )

            _ = [event async for event in GoalSupervisor(store, run_once).run("开始")]
            self.assertEqual(calls, 1)

    async def test_coding_assistant_wires_verification_and_completion_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = ScriptedModelClient(
                [
                    AssistantReply(content="先报告进度"),
                    AssistantReply(
                        tool_calls=[
                            ToolInvocation(
                                "verify-1",
                                "bash",
                                {
                                    "command": "python -c \"print('verified')\"",
                                    "goal_verification": True,
                                },
                            )
                        ],
                        stop_reason="tool_calls",
                    ),
                    AssistantReply(
                        tool_calls=[
                            ToolInvocation(
                                "complete-1",
                                "goal_complete",
                                {
                                    "final_result": "完成",
                                    "criteria_evidence": [
                                        {"criterion": "验证成功", "evidence": "verified"}
                                    ],
                                },
                            )
                        ],
                        stop_reason="tool_calls",
                    ),
                    AssistantReply(content="Goal 已完成"),
                ]
            )
            assistant = CodingAssistant(
                client,
                ModelProfile("fake"),
                directory,
                runtime_settings=RuntimeSettings(backend="host"),
                goal_config=GoalConfig(max_attempts=4),
            )
            assistant.create_goal("完成集成验证", ["验证成功"])
            _ = [event async for event in assistant.run_goal("开始")]
            state = assistant.goal_store.read()
            self.assertEqual(state.status, "complete")  # type: ignore[union-attr]
            self.assertEqual(state.attempt_count, 2)  # type: ignore[union-attr]
            self.assertTrue(state.last_verification.passed)  # type: ignore[union-attr]
            self.assertEqual(len(client.requests), 4)


class GoalCommandAndConfigTests(unittest.TestCase):
    def test_chinese_goal_command_and_criteria(self) -> None:
        command = parse_goal_command("目标 修复登录\n验收条件：\n- 正常登录成功\n- 错误密码被拒绝")
        self.assertEqual(command.action, "start")  # type: ignore[union-attr]
        self.assertEqual(command.goal, "修复登录")  # type: ignore[union-attr]
        self.assertEqual(command.acceptance_criteria, ["正常登录成功", "错误密码被拒绝"])  # type: ignore[union-attr]
        self.assertEqual(parse_goal_command("/goal status").action, "status")  # type: ignore[union-attr]

    def test_miniclaw_goal_environment(self) -> None:
        config = load_goal_config(
            {
                "MINICLAW_GOAL_MAX_ATTEMPTS": "7",
                "MINICLAW_GOAL_MAX_DURATION_SECONDS": "90",
                "MINICLAW_GOAL_MAX_COST_USD": "2.5",
            }
        )
        self.assertEqual(config.max_attempts, 7)
        self.assertEqual(config.max_duration_seconds, 90)
        judge = load_goal_judge_config(
            {
                "MINICLAW_GOAL_JUDGE_ENABLED": "true",
                "MINICLAW_GOAL_JUDGE_MODEL": "judge-model",
                "MINICLAW_GOAL_JUDGE_TIMEOUT": "5",
            }
        )
        self.assertTrue(judge.enabled)
        self.assertEqual(judge.timeout_seconds, 5)

    def test_judge_parser_fails_closed_on_inconsistent_result(self) -> None:
        approved = GoalJudge._parse(
            '{"approved":true,"summary":"ok","criteria":[{"criterion":"A","passed":true,"reason":"ok"}]}',
            ["A"],
        )
        self.assertTrue(approved.approved)
        with self.assertRaisesRegex(RuntimeError, "inconsistent"):
            GoalJudge._parse(
                '{"approved":true,"summary":"bad","criteria":[{"criterion":"A","passed":false,"reason":"missing"}]}',
                ["A"],
            )


if __name__ == "__main__":
    unittest.main()
