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
            self.assertEqual(context.last_compaction_decision['reason'], 'soft_requires_model')
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


if __name__ == '__main__':
    unittest.main()
