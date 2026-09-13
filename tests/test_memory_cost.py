from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from MiniClaw.llm.types import AssistantReply, ChatMessage, ModelEvent, ModelProfile, ToolInvocation
from MiniClaw.coding_agent.memory.config import MemoryConfig
from MiniClaw.coding_agent.memory.working import WorkingContext, estimate_context_tokens
from MiniClaw.coding_agent.memory.manager import MemoryManager


class SummaryClient:
    def __init__(self, text='Retain the unique business constraint: preserve output on errors.'):
        self.requests = []
        self.text = text

    async def stream(self, request):
        self.requests.append(request)
        yield ModelEvent(type='completed', reply=AssistantReply(content=self.text))


def make_context(root, client=None, **overrides):
    values = dict(enabled=True, reserve_tokens=1024, keep_recent_tokens=500,
                  soft_trigger_tokens=2000, hard_trigger_tokens=3000,
                  target_tokens=1600, progressive_enabled=True,
                  artifact_threshold_bytes=4096, artifact_preview_chars=300,
                  deterministic_semantic_tokens=200)
    values.update(overrides)
    return WorkingContext(path=root/'session.jsonl', workspace=root, session_id='cost',
                          config=MemoryConfig(**values), model_client=client or SummaryClient(),
                          profile=ModelProfile('fake', context_window=10000, max_output_tokens=1024))


class MemoryCostTests(unittest.IsolatedAsyncioTestCase):
    async def test_cumulative_input_watermark_triggers_and_resets_after_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            context = make_context(
                root,
                soft_trigger_tokens=2_000,
                hard_trigger_tokens=3_000,
                target_tokens=400,
                keep_recent_tokens=100,
            )
            messages = [
                ChatMessage(role="user", content="old constraint " * 120),
                ChatMessage(role="assistant", content="old result " * 120),
                ChatMessage(role="user", content="current request"),
                ChatMessage(role="assistant", content="current result"),
            ]
            for message in messages:
                context.append_message(message)

            self.assertEqual(context.record_model_input(1_500), 1_500)
            self.assertIsNone(
                await context.maybe_compact(
                    messages,
                    provider_input_tokens=100,
                    cumulative_input_tokens=context.cumulative_input_tokens,
                )
            )
            self.assertEqual(context.record_model_input(600), 2_100)
            outcome = await context.maybe_compact(
                messages,
                provider_input_tokens=100,
                cumulative_input_tokens=context.cumulative_input_tokens,
            )
            self.assertIsNotNone(outcome)
            self.assertEqual(context.cumulative_input_tokens, 0)

    async def test_soft_watermark_archives_two_medium_tool_results_without_closing_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            context = make_context(root, soft_trigger_tokens=100, hard_trigger_tokens=200)
            first = ToolInvocation("tool-1", "bash", {"command": "one"})
            second = ToolInvocation("tool-2", "read", {"path": "two"})
            messages = [
                ChatMessage(role="user", content="request"),
                ChatMessage(role="assistant", tool_calls=[first]),
                ChatMessage(role="tool", name="bash", tool_call_id="tool-1", content="a" * 2048),
                ChatMessage(role="assistant", tool_calls=[second]),
                ChatMessage(role="tool", name="read", tool_call_id="tool-2", content="b" * 2048),
                ChatMessage(role="assistant", content="continue"),
            ]
            for message in messages:
                context.append_message(message)
            outcome = await context.maybe_compact(messages, cumulative_input_tokens=100)
            self.assertIsNotNone(outcome)
            assert outcome is not None
            self.assertEqual(outcome.strategy, "tool-artifact")
            self.assertEqual(outcome.details["archived_tool_count"], 2)
            self.assertEqual(len(outcome.messages), len(messages))
            self.assertTrue(all(
                "[MiniClaw context artifact]" in message.content
                for message in outcome.messages
                if message.role == "tool"
            ))

    async def test_soft_watermark_with_fewer_than_two_medium_tools_closes_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            context = make_context(root, soft_trigger_tokens=100, hard_trigger_tokens=200)
            call = ToolInvocation("tool-1", "bash", {"command": "one"})
            messages = [
                ChatMessage(role="user", content="request " * 500),
                ChatMessage(role="assistant", tool_calls=[call]),
                ChatMessage(role="tool", name="bash", tool_call_id="tool-1", content="a" * 2048),
                ChatMessage(role="assistant", content="continue " * 500),
            ]
            for message in messages:
                context.append_message(message)
            outcome = await context.maybe_compact(messages, cumulative_input_tokens=100)
            self.assertIsNotNone(outcome)
            assert outcome is not None
            self.assertEqual(outcome.details["reason"], "soft")
            self.assertIn("closed-history-archive", outcome.details["layers_applied"])

    def test_consolidation_delta_keeps_tool_call_result_pair(self):
        call = ToolInvocation("call-1", "bash", {"command": "false"})
        messages = [
            ChatMessage(role="user", content="keep this constraint"),
            ChatMessage(role="assistant", tool_calls=[call]),
            ChatMessage(role="tool", name="bash", tool_call_id="call-1", content="failed"),
        ]
        delta = MemoryManager._consolidation_delta(messages, 2)
        self.assertEqual([m.role for m in delta], ["assistant", "tool"])
        self.assertEqual(delta[1].tool_call_id, delta[0].tool_calls[0].call_id)

    async def test_request_transform_artifactizes_completed_arguments_and_results_without_mutating_history(self):
        with tempfile.TemporaryDirectory() as folder:
            context = make_context(Path(folder), artifact_threshold_bytes=64)
            call = ToolInvocation("call-1", "bash", {"command": "x" * 4000})
            raw = [ChatMessage(role="assistant", tool_calls=[call]), ChatMessage(
                role="tool", name="bash", tool_call_id="call-1", content="successful output " * 300)]
            transformed = context.transform_request_context(raw)
            self.assertNotIn("[MiniClaw context artifact]", call.arguments["command"])
            self.assertIn("[MiniClaw context artifact]", transformed[0].tool_calls[0].arguments["command"])
            self.assertIn("[MiniClaw context artifact]", transformed[1].content)
            self.assertNotIn("[MiniClaw context artifact]", raw[1].content)

    async def test_retained_large_request_is_not_repeated_and_history_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            context = make_context(root)
            latest = 'UNIQUE_LATEST_REQUEST: ' + 'keep every constraint ' * 300
            messages = [ChatMessage(role='user', content='preserve output on errors ' * 800),
                        ChatMessage(role='assistant', content='old analysis ' * 300),
                        ChatMessage(role='user', content=latest),
                        ChatMessage(role='assistant', content='acknowledged')]
            for message in messages:
                context.append_message(message)
            result = await context.maybe_compact(messages, 10000)
            self.assertIsNotNone(result)
            self.assertEqual(sum(m.content.count('UNIQUE_LATEST_REQUEST:') for m in result.messages), 1)
            self.assertLess(estimate_context_tokens(result.messages), estimate_context_tokens(messages))
            self.assertEqual(result.details['estimated_history_tokens_saved'],
                             estimate_context_tokens(messages) - estimate_context_tokens(result.messages))
            recovered = make_context(root).load()
            self.assertEqual([m.content for m in recovered], [m.content for m in result.messages])
            archived = root / result.details['archive']['path']
            self.assertIn('preserve output on errors', archived.read_text(encoding='utf-8'))

    async def test_soft_deferral_has_no_archive_index_or_fact_side_effects(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            context = make_context(root)
            context.archive_index = Mock()
            context.stable_fact_ingestor = Mock()
            messages = [ChatMessage(role='user', content='old dense requirements ' * 300),
                        ChatMessage(role='assistant', content='old answer ' * 100),
                        ChatMessage(role='user', content='continue'),
                        ChatMessage(role='assistant', content='recent ' * 300)]
            for message in messages:
                context.append_message(message)
            original = context.path.read_bytes()
            for _ in range(3):
                self.assertIsNone(await context.maybe_compact(messages, 2500))
            self.assertEqual(context.last_compaction_decision['reason'], 'soft_requires_hard_pressure')
            self.assertFalse(context.artifacts.root.exists())
            context.archive_index.add_messages.assert_not_called()
            context.stable_fact_ingestor.ingest.assert_not_called()
            self.assertEqual(context.path.read_bytes(), original)
            self.assertEqual(len(context.model_client.requests), 0)

    async def test_expanding_summary_is_rejected_and_same_prefix_not_resummarized(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            client = SummaryClient('expanding response ' * 2000)
            context = make_context(root, client, keep_recent_tokens=10)
            messages = [ChatMessage(role='user', content='old business constraints ' * 350),
                        ChatMessage(role='assistant', content='old work ' * 100),
                        ChatMessage(role='user', content='latest'),
                        ChatMessage(role='assistant', content='recent')]
            for message in messages:
                context.append_message(message)
            original = context.path.read_bytes()
            self.assertIsNone(await context.maybe_compact(messages, 5000))
            self.assertEqual(context.last_compaction_decision['reason'], 'insufficient_token_savings')
            self.assertIsNone(await context.maybe_compact(messages, 7000))
            self.assertEqual(context.last_compaction_decision['reason'], 'unchanged_low_gain_prefix')
            self.assertEqual(len(client.requests), 1)
            self.assertEqual(context.path.read_bytes(), original)
            self.assertFalse(context.artifacts.root.exists())
            self.assertEqual([m.content for m in make_context(root).load()], [m.content for m in messages])

    async def test_provider_overhead_alone_cannot_justify_expanding_history(self):
        with tempfile.TemporaryDirectory() as folder:
            context = make_context(Path(folder), keep_recent_tokens=1)
            messages = [ChatMessage(role='user', content='hi'), ChatMessage(role='assistant', content='hello')]
            for m in messages:
                context.append_message(m)
            self.assertIsNone(await context.maybe_compact(messages, 50000))
            self.assertFalse(context.artifacts.root.exists())
            records = [json.loads(line) for line in context.path.read_text().splitlines()]
            self.assertTrue(all(r['type'] == 'message' for r in records))

    async def test_phase_boundary_uses_one_deterministic_handoff_and_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            context = make_context(root)
            call = ToolInvocation("phase-call", "bash", {"command": "pytest -q"})
            messages = [
                ChatMessage(role="user", content="preserve compatibility and verify rollback"),
                ChatMessage(role="assistant", content="I inspected the implementation.", tool_calls=[call]),
                ChatMessage(role="tool", name="bash", tool_call_id="phase-call", content="2 passed"),
                ChatMessage(role="assistant", content="The phase is complete."),
            ]
            for message in messages:
                context.append_message(message)

            outcome = await context.compact_phase(messages, {
                "confirmed_constraints": ["preserve compatibility"],
                "completed_changes": ["engine.py"],
                "unverified_boundaries": [],
                "next_action": "inspect the next phase",
            })

            self.assertIsNotNone(outcome)
            assert outcome is not None
            self.assertEqual(len(outcome.messages), 1)
            self.assertIn("Phase Handoff", outcome.messages[0].content)
            self.assertIn("Status: COMPLETE", outcome.messages[0].content)
            self.assertIn("engine.py", outcome.messages[0].content)
            self.assertIn("do not repeat", outcome.messages[0].content.lower())
            self.assertNotIn("Historical agent notes", outcome.messages[0].content)
            self.assertEqual(outcome.strategy, "deterministic-archive")
            self.assertTrue((root / outcome.details["archive"]["path"]).exists())
            recovered = make_context(root).load()
            self.assertEqual([m.content for m in recovered], [m.content for m in outcome.messages])

    async def test_phase_handoff_uses_real_mutation_and_keeps_failed_verification_open(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            context = make_context(root)
            edit = ToolInvocation("edit-1", "edit", {
                "path": "engine.py",
                "edits": [{"oldText": "old", "newText": "new"}],
            })
            verify = ToolInvocation("verify-1", "bash", {"command": "pytest -q"})
            messages = [
                ChatMessage(role="user", content="fix the engine"),
                ChatMessage(role="assistant", tool_calls=[edit]),
                ChatMessage(role="tool", name="edit", tool_call_id="edit-1", content="updated"),
                ChatMessage(role="assistant", tool_calls=[verify]),
                ChatMessage(role="tool", name="bash", tool_call_id="verify-1", content="1 failed"),
                ChatMessage(role="assistant", content="The verification still fails."),
            ]
            for message in messages:
                context.append_message(message)
            context._failed_call_ids.add("verify-1")

            outcome = await context.compact_phase(messages, {
                "confirmed_constraints": ["preserve compatibility"],
                "completed_changes": [],
                "unverified_boundaries": [],
                "next_action": "fix the failing assertion",
            })

            self.assertIsNotNone(outcome)
            assert outcome is not None
            handoff = outcome.messages[0].content
            self.assertIn("Status: INCOMPLETE", handoff)
            self.assertIn("engine.py", handoff)
            self.assertIn("[FAIL] pytest -q", handoff)
            self.assertIn("fix the failing assertion", handoff)

    async def test_model_checkpoint_prompt_is_an_executable_bounded_state_transfer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            client = SummaryClient(
                "Status: IN_PROGRESS\n"
                "## Current objective\nFinish the fix.\n"
                "## Confirmed constraints\nPreserve rollback.\n"
                "## Completed work\nUpdated engine.py.\n"
                "## Latest valid verification\nPASS: focused test.\n"
                "## Remaining or failed\nNone.\n"
                "## Next action\nRun the focused test.\n"
                "## Necessary files and artifacts\nengine.py."
            )
            context = make_context(root, client)
            await context._model_checkpoint(
                "finish the fix",
                [ChatMessage(role="user", content="preserve rollback behavior")],
                None,
                ["engine.py"],
                ["engine.py"],
                summary_budget_tokens=20_000,
            )

            prompt = client.requests[0].messages[1].content
            self.assertIn("executable continuation checkpoint", prompt)
            self.assertIn("Status: IN_PROGRESS", prompt)
            self.assertIn("## Latest valid verification", prompt)
            self.assertIn("one concrete smallest next action", prompt)
            self.assertIn("at most 20000 tokens", prompt)

    async def test_historical_duplicate_bash_and_failed_edit_arguments_are_compact(self):
        with tempfile.TemporaryDirectory() as folder:
            context = make_context(Path(folder))
            first = ToolInvocation("bash-1", "bash", {"command": "pytest -q"})
            second = ToolInvocation("bash-2", "bash", {"command": "pytest -q"})
            failed = ToolInvocation("edit-1", "edit", {
                "path": "x.py",
                "edits": [{"oldText": "OLD " * 1000, "newText": "NEW " * 1000}],
            })
            messages = [
                ChatMessage(role="assistant", tool_calls=[first]),
                ChatMessage(role="tool", name="bash", tool_call_id="bash-1", content="same output " * 100),
                ChatMessage(role="assistant", tool_calls=[second]),
                ChatMessage(role="tool", name="bash", tool_call_id="bash-2", content="same output " * 100),
                ChatMessage(role="assistant", tool_calls=[failed]),
                ChatMessage(role="tool", name="edit", tool_call_id="edit-1", content="edit failed"),
            ]
            context._failed_call_ids.add("edit-1")
            projected = context.transform_request_context(messages)
            self.assertGreater(context.last_projection["duplicate_tool_results_removed"], 0)
            self.assertIn("Historical duplicate", projected[1].content)
            self.assertNotIn("OLD ", json.dumps(projected[4].tool_calls[0].arguments))


if __name__ == '__main__':
    unittest.main()
