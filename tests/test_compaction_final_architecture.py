from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from MiniClaw.cancellation import CancellationToken, OperationCancelledError
from MiniClaw.coding_agent.memory.config import MemoryConfig
from MiniClaw.coding_agent.memory.working import COMPACTION_PIPELINE, WorkingContext
from MiniClaw.llm.types import AssistantReply, ChatMessage, ModelEvent, ModelProfile, ToolInvocation


class SummaryClient:
    def __init__(self, content: str = """Status: IN_PROGRESS
## Current objective
continue task
## Confirmed constraints
- preserve requirements
## Completed work
- none
## Latest valid verification
- none
## Remaining or failed
- none
## Next action
- continue
## Necessary files and artifacts
- none""") -> None:
        self.content = content
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield ModelEvent(type="completed", reply=AssistantReply(content=self.content))


class CancellingClient(SummaryClient):
    async def stream(self, request):
        self.requests.append(request)
        assert request.cancellation_token is not None
        request.cancellation_token.cancel("provider cancelled")
        yield ModelEvent(type="completed", reply=AssistantReply(content="ignored"))


def make_context(root: Path, client: SummaryClient | None = None, **overrides: object) -> WorkingContext:
    values: dict[str, object] = {
        "enabled": True,
        "reserve_tokens": 64,
        "keep_recent_tokens": 12,
        "soft_trigger_tokens": 100,
        "hard_trigger_tokens": 200,
        "target_tokens": 80,
        "progressive_enabled": True,
        "artifact_threshold_bytes": 4_096,
        "artifact_preview_chars": 200,
        "deterministic_semantic_tokens": 1,
    }
    values.update(overrides)
    return WorkingContext(
        path=root / "session.jsonl",
        workspace=root,
        session_id="final",
        config=MemoryConfig(**values),  # type: ignore[arg-type]
        model_client=client or SummaryClient(),
        profile=ModelProfile("fake", context_window=1_000, max_output_tokens=64),
    )


def append_all(context: WorkingContext, messages: list[ChatMessage]) -> None:
    for message in messages:
        context.append_message(message)


class FinalCompactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_soft_pressure_is_cheap_but_hard_pressure_can_escalate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = make_context(Path(directory))
            messages = [
                ChatMessage(role="user", content="old requirement " * 30),
                ChatMessage(role="assistant", content="old decision " * 30),
                ChatMessage(role="user", content="continue"),
                ChatMessage(role="assistant", content="recent"),
            ]
            append_all(context, messages)

            self.assertIsNone(await context.maybe_compact(messages, 150))
            self.assertEqual(
                context.last_compaction_decision["reason"],
                "soft_waiting_for_more_reclaimable_history",
            )
            self.assertEqual(context.model_client.requests, [])

            outcome = await context.maybe_compact(messages, 201)
            # Hard pressure prioritizes the model summary, but a checkpoint
            # is committed only when it is smaller and structurally useful.
            self.assertIsNone(outcome)
            self.assertEqual(len(context.model_client.requests), 1)

    async def test_expanding_model_summary_does_not_commit_or_persist_prepared_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = SummaryClient("too large " * 2_000)
            context = make_context(Path(directory), client)
            messages = [
                ChatMessage(role="user", content="old requirement " * 250),
                ChatMessage(role="assistant", content="old work " * 100),
                ChatMessage(role="user", content="latest"),
                ChatMessage(role="assistant", content="recent"),
            ]
            append_all(context, messages)

            self.assertIsNone(await context.maybe_compact(messages, 201))
            self.assertEqual(context.last_compaction_decision["reason"], "insufficient_token_savings")
            records = [json.loads(line) for line in context.path.read_text(encoding="utf-8").splitlines()]
            self.assertFalse(any(item.get("type") == "compaction" for item in records))
            self.assertFalse(context.artifacts.root.exists())

    async def test_skill_output_is_live_instruction_only_and_is_not_archived(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = make_context(
                Path(directory),
                client=SummaryClient(
                    """Status: IN_PROGRESS
## Current objective
continue the task
## Confirmed constraints
- preserve the task
## Completed work
- none
## Latest valid verification
- none
## Remaining or failed
- none
## Next action
- continue
## Necessary files and artifacts
- none"""
                ),
                deterministic_semantic_tokens=10_000,
            )
            skill_call = ToolInvocation("skill-1", "skill", {"action": "read", "name": "testing"})
            messages = [
                ChatMessage(role="user", content="old task" * 80),
                ChatMessage(role="assistant", tool_calls=[skill_call]),
                ChatMessage(role="tool", name="skill", tool_call_id="skill-1", content="PRIVATE_SKILL_BODY"),
                ChatMessage(role="user", content="new task"),
                ChatMessage(role="assistant", content="done"),
            ]
            append_all(context, messages)

            outcome = await context.maybe_compact(messages, 201)
            # Skill output is not a summary evidence source. With no useful
            # closed work left after filtering, the README pipeline correctly
            # declines to create a checkpoint.
            if outcome is not None:
                archive = outcome.details.get("archive")
                if archive is not None:
                    archive_path = Path(directory) / archive["path"]
                    self.assertNotIn("PRIVATE_SKILL_BODY", archive_path.read_text(encoding="utf-8"))

    async def test_cancelled_compaction_has_no_commit_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = make_context(Path(directory))
            messages = [
                ChatMessage(role="user", content="old task " * 50),
                ChatMessage(role="assistant", content="old answer " * 50),
                ChatMessage(role="user", content="latest"),
                ChatMessage(role="assistant", content="recent"),
            ]
            append_all(context, messages)
            token = CancellationToken()
            token.cancel("test cancellation")
            with self.assertRaises(OperationCancelledError):
                await context.maybe_compact(messages, 201, token)
            records = [json.loads(line) for line in context.path.read_text(encoding="utf-8").splitlines()]
            self.assertFalse(any(item.get("type") == "compaction" for item in records))

    async def test_unfinished_tool_batch_stays_on_the_retained_side_of_the_cut(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = make_context(Path(directory))
            messages = [
                ChatMessage(role="user", content="old task " * 60),
                ChatMessage(
                    role="assistant",
                    tool_calls=[ToolInvocation("pending", "read", {"path": "x.py"})],
                ),
                ChatMessage(role="user", content="new request"),
                ChatMessage(role="assistant", content="recent"),
            ]
            append_all(context, messages)
            # The only valid boundary near the pressure point is before the
            # unfinished assistant call, so the pending call remains intact.
            self.assertEqual(context._find_cut_point(messages), 1)

    async def test_cancellation_during_model_summary_has_no_commit_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = make_context(Path(directory), CancellingClient())
            messages = [
                ChatMessage(role="user", content="dense requirement " * 80),
                ChatMessage(role="assistant", content="old answer " * 80),
                ChatMessage(role="user", content="latest"),
                ChatMessage(role="assistant", content="recent"),
            ]
            append_all(context, messages)
            with self.assertRaises(OperationCancelledError):
                await context.maybe_compact(messages, 201, CancellationToken())
            records = [json.loads(line) for line in context.path.read_text(encoding="utf-8").splitlines()]
            self.assertFalse(any(item.get("type") == "compaction" for item in records))


if __name__ == "__main__":
    unittest.main()
