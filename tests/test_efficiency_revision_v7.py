"""Intermediate protocol/business failures remain recoverable within the original budget."""
import sys
import tempfile
import unittest
from pathlib import Path

from MiniClaw.llm.types import ToolInvocation, AssistantReply
from tests.test_agent_loop import ScriptedModelClient
from tests import test_task_recovery as helpers


class ErrorContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def test_alternating_protocol_and_business_errors_continue_to_correct_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'probe.py').write_text("import pathlib\np=pathlib.Path('attempt')\nn=int(p.read_text()) if p.exists() else 0\np.write_text(str(n+1))\nprint(['no JSON','{\"wrong\":5}','no JSON','{\"a\":4}','{\"wrong\":5}','{\"a\":5}'][n])\n", encoding='utf-8')
            spec = [{'criterion_id':'a', 'artifacts':['probe.py'], 'comparisons':[
                {'check_id':'a', 'expected':5, 'expected_source':'formal input'}]}]
            replies = [AssistantReply(tool_calls=[ToolInvocation('init', 'task_checkpoint', {
                'summary':'verify', 'remaining':[], 'next_action':'check', 'criteria':[
                    {'criterion_id':'a', 'description':'A is five', 'kind':'data', 'check_ids':['a']} ]})])]
            replies += [AssistantReply(tool_calls=[ToolInvocation(str(i), 'bash', {
                'command':f'"{sys.executable}" -X utf8 probe.py', 'verification':spec})]) for i in range(6)]
            replies += [AssistantReply(content='Verified A is five.')]
            model = ScriptedModelClient(replies)
            assistant = helpers.RecoveryTests().assistant(model, d)
            events = [e async for e in assistant.run('Verify A')]
            self.assertEqual(len(model.requests), 8)
            self.assertEqual((root/'attempt').read_text(), '6')
            self.assertEqual(assistant.loop.max_turns, 32)
            self.assertEqual(assistant.task_progress.state['status'], 'completed')
            self.assertFalse(any((e.details or {}).get('pause_reason') == 'verification_stalled' for e in events))
            content = '\n'.join(m.content or '' for m in model.requests[6].messages if m.role == 'tool')
            self.assertIn('found 0', content)
            self.assertIn('Missing observation key', content)
            self.assertIn('"actual": 4', content)
            self.assertEqual(events[-1].message.content, 'Verified A is five.')
