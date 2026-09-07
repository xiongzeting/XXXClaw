import tempfile
import unittest
from pathlib import Path

from MiniClaw.coding_agent.memory.manager import MemoryManager
from MiniClaw.llm.types import ChatMessage, ModelProfile, ToolInvocation
from tests.test_memory_distillation import ConsolidationModelClient


class IncrementalConsolidationTests(unittest.IsolatedAsyncioTestCase):
    def manager(self, root, model):
        return MemoryManager(workspace=root, session_path=root / '.aster/session.jsonl', session_id='incremental',
            model_client=model, profile=ModelProfile('fake', context_window=8000, max_output_tokens=2048),
            environment={'MINICLAW_MEMORY_ENABLED':'true', 'MINICLAW_MEMORY_CONSOLIDATION_ENABLED':'true',
                'MINICLAW_MEMORY_VECTOR_ENABLED':'false', 'MINICLAW_MEMORY_CROSS_ENCODER_ENABLED':'false'})

    async def test_no_delta_and_small_talk_after_restart_keep_prior_summary_without_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model = ConsolidationModelClient()
            manager = self.manager(root, model)
            messages = [ChatMessage(role='user', content='Project database uses PostgreSQL.'),
                        ChatMessage(role='assistant', content='Migration verified.')]
            for message in messages:
                manager.append(message)
            first = await manager.finalize_session(messages, status='completed')
            self.assertEqual(len(model.requests), 1)
            second = await manager.finalize_session(messages, status='completed')
            self.assertEqual(second.summary, first.summary)
            self.assertEqual(len(model.requests), 1)
            restarted = self.manager(root, model)
            restored = restarted.load_context()
            self.assertEqual(len(restored), 2)
            restored.extend([ChatMessage(role='user', content='谢谢，收到'), ChatMessage(role='assistant', content='不客气')])
            for message in restored[2:]:
                restarted.append(message)
            third = await restarted.finalize_session(restored, status='completed')
            self.assertEqual(len(model.requests), 1)
            self.assertEqual(third.summary, first.summary)
            self.assertEqual(third.details['consolidation_messages_processed'], 2)
            self.assertEqual(third.details['consolidation_decision'], 'no_new_substantive_evidence')
            preference = ChatMessage(role='user', content='请记住：以后默认用中文回答。')
            restored.append(preference)
            restarted.append(preference)
            fourth = await restarted.finalize_session(restored, status='completed')
            self.assertEqual(len(model.requests), 2)
            self.assertEqual(fourth.details['consolidation_messages_processed'], 1)

    async def test_new_tool_evidence_is_consolidated_as_delta(self):
        with tempfile.TemporaryDirectory() as folder:
            model = ConsolidationModelClient()
            manager = self.manager(Path(folder), model)
            messages = [ChatMessage(role='user', content='Project database uses PostgreSQL.')]
            await manager.finalize_session(messages, status='completed')
            messages.extend([ChatMessage(role='assistant', tool_calls=[ToolInvocation('v','bash',{'command':'test'})]),
                ChatMessage(role='tool', tool_call_id='v', name='bash', content='new verification succeeded')])
            result = await manager.finalize_session(messages, status='completed')
            self.assertEqual(len(model.requests), 2)
            self.assertEqual(result.details['consolidation_messages_processed'], 2)
