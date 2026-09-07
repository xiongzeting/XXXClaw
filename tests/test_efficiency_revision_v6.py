"""Observed envelope/binding failures and alternating retries, without model API calls."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from MiniClaw.coding_agent.assistant.progress import TaskProgress
from MiniClaw.coding_agent.tools.base import ToolResult
from MiniClaw.llm.types import ToolInvocation, AssistantReply
from tests.test_agent_loop import ScriptedModelClient
from tests import test_task_recovery as helpers


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'data.json').write_text('{"net":5}', encoding='utf-8')
        self.p = TaskProgress(self.root/'.session', self.root)
        self.p.begin_user_turn('Check net against input')
        self.criteria = [{'criterion_id': k, 'description': k, 'version': 1, 'kind': 'data', 'check_ids': [k]}
                         for k in ['net', 'other']]
        self.p.checkpoint('verify', [], 'check', self.criteria[:1])

    def spec(self, key='net', path=None, expected=5):
        c = {'check_id': key, 'expected': expected, 'expected_source': 'formal input'}
        if path is not None: c['pointer'] = path
        return {'criterion_id': key, 'artifacts': ['data.json'], 'comparisons': [c]}

    def execute(self, output, specs=None, **details):
        specs = specs or [self.spec()]
        result = ToolResult(output, details={'stdout': output, 'stderr': '', 'exit_code': 0,
            'verification_plan': self.p.prepare_verification(specs), 'verifier_sha256': 'original-command-hash',
            'workspace_changes': {'complete': True, 'changed_paths': []}, **details})
        self.p.observe(ToolInvocation('original', 'bash', {'verification': specs}), result)
        return result

    def replay(self, identifier, path='/actual'):
        result = self.p.rebind_verification(identifier, [{'criterion_id': 'net', 'check_id': 'net', 'pointer': path}])
        self.p.observe(ToolInvocation('repair', 'verification_rebind', {}), result)
        return result

    def test_bare_wrapped_and_redundant_wrapper_paths(self):
        for wrapped in [False, True]:
            for path in [None, '/net', '/observations/net']:
                with self.subTest(wrapped=wrapped, path=path):
                    value = {'net': 5}
                    self.execute(json.dumps({'observations': value} if wrapped else value), [self.spec(path=path)])
                    self.assertIsNone(self.p.final_blocker())

    def test_ambiguous_pointer_is_not_guessed_even_with_equal_values(self):
        for nested in [5, 6]:
            self.execute(json.dumps({'observations': {'net': 5, 'observations': {'net': nested}}}),
                         [self.spec(path='/observations/net')])
            self.assertIn('Ambiguous', self.p.state['verification_error'])
            self.assertTrue(self.p.pending())

    def test_invalid_records_never_become_pass_or_reusable_evidence(self):
        for output, details in [('{"net":5}\n{"net":5}', {}), ('{"net":5,"net":6}', {}),
                ('{"net":5} EXTRA', {}), ('{"net":', {}), ('{"net":5}', {'capture_truncated': True}),
                ('{"net":5}', {'exit_code': 7}), ('{"net":5}', {'stderr': '{"net":5}'}),
                ('{"observations":{"net":5},"net":5}', {}), ('[5]', {}), ('no JSON', {})]:
            with self.subTest(output=output, details=details):
                self.p.state['verified'] = {}
                result = self.execute(output, **details)
                self.assertIsNotNone(self.p.final_blocker())
                self.assertNotIn('evidence_id', result.details)

    def test_evidence_rebind_keeps_original_identity_and_file_capture(self):
        result = self.execute('{"actual":5}', [self.spec(path='/missing')])
        identifier = result.details['evidence_id']
        raw = (self.p.evidence_store.root/(identifier+'.json')).read_bytes()
        replay = self.replay(identifier)
        self.assertIsNone(self.p.final_blocker())
        self.assertTrue(replay.details['not_started'])
        proof = self.p.state['verified']['net']
        self.assertEqual(proof['call_id'], 'original')
        self.assertEqual(proof['rebind_call_id'], 'repair')
        self.assertEqual(proof['verifier_sha256'], 'original-command-hash')
        self.assertEqual(raw, (self.p.evidence_store.root/(identifier+'.json')).read_bytes())
        restored = TaskProgress(self.p.path.parent, self.root)
        self.assertTrue(restored.rebind_verification(identifier,
            [{'criterion_id': 'net', 'check_id': 'net', 'pointer': '/actual'}]).details['evidence_reused'])

    def test_actual_mismatch_after_rebind_stays_failed(self):
        result = self.execute('{"actual":4}', [self.spec(path='/missing')])
        self.replay(result.details['evidence_id'])
        self.assertNotIn('verification_error', self.p.state)
        self.assertIsNotNone(self.p.final_blocker())
        self.assertEqual(self.p.state['failed_observations']['net'][0]['actual'], 4)

    def test_changed_or_missing_artifact_rejects_replay(self):
        result = self.execute('{"actual":5}', [self.spec(path='/missing')])
        (self.root/'data.json').write_text('{"net":6}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed'): self.replay(result.details['evidence_id'])
        (self.root/'data.json').unlink()
        with self.assertRaises(ValueError): self.replay(result.details['evidence_id'])

    def test_new_user_revision_or_withdrawal_rejects_replay(self):
        result = self.execute('{"actual":5}', [self.spec(path='/missing')])
        self.p.begin_user_turn('Withdraw the previous requirement')
        self.p.checkpoint('withdraw', [], 'stop', [], withdrawn=['net'], requirement_revision=2)
        with self.assertRaisesRegex(ValueError, 'instructions changed'): self.replay(result.details['evidence_id'])

    def test_version_staleness_and_unknown_ids_rejected(self):
        result = self.execute('{"actual":5}', [self.spec(path='/missing')])
        self.p.state['criteria'][0]['version'] += 1
        with self.assertRaisesRegex(ValueError, 'Requirement changed'): self.replay(result.details['evidence_id'])
        with self.assertRaisesRegex(ValueError, 'Unknown'): self.replay('a'*32)
        other = TaskProgress(self.root/'.other', self.root)
        with self.assertRaisesRegex(ValueError, 'Unknown'): other.rebind_verification(result.details['evidence_id'], [])

    def test_tampered_evidence_cannot_be_replayed(self):
        result = self.execute('{"actual":5}', [self.spec(path='/missing')])
        path = self.p.evidence_store.root/(result.details['evidence_id']+'.json')
        path.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Evidence capture changed'): self.replay(result.details['evidence_id'])

    def test_rebinding_cannot_edit_expectations_or_manifest(self):
        result = self.execute('{"actual":5}', [self.spec(path='/missing')])
        for field in ['expected', 'expected_source', 'artifacts', 'version', 'final_pointer', 'operator']:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'expectations stay fixed'):
                self.p.rebind_verification(result.details['evidence_id'], [
                    {'criterion_id': 'net', 'check_id': 'net', 'pointer': '/actual', field: 'override'}])

    def test_no_manifest_allows_original_check_but_not_reuse(self):
        spec = self.spec(); spec['artifacts'] = []
        result = self.execute('{"net":5}', [spec])
        self.assertIsNone(self.p.final_blocker())
        self.assertNotIn('evidence_id', result.details)
        self.assertIn('artifact', result.details['evidence_reuse_unavailable'])

    def test_alternating_errors_and_verifier_edits_do_not_reset_counter(self):
        for i, output in enumerate(['{"wrong":5}', 'no JSON', '{"wrong_again":5}']):
            self.execute(output)
            self.p.observe(ToolInvocation(str(i), 'edit', {'path': 'probe.py'}), ToolResult('edited', details={
                'workspace_changes': {'complete': True, 'changed_paths': ['probe.py']}}))
        self.assertEqual(self.p.state['verification_retry']['count'], 3)
        restored = TaskProgress(self.p.path.parent, self.root)
        self.assertEqual(restored.state['verification_retry']['count'], 3)
        restored.begin_user_turn('Continue debugging')
        self.assertNotIn('verification_retry', restored.state)

    def test_rechecking_good_sibling_does_not_clear_other_errors(self):
        self.p.checkpoint('two', [], 'verify', self.criteria)
        both = [self.spec(), self.spec('other')]
        self.execute('{"net":5}', both)
        self.execute('{"net":5}', [self.spec()])
        self.execute('no JSON', [self.spec('other')])
        self.assertEqual(self.p.state['verification_retry']['count'], 3)
        self.assertIn('net', self.p.state['verified'])
        self.assertIn('other', self.p.state['verification_errors'])
        self.execute('{"other":5}', [self.spec('other')])
        self.assertNotIn('verification_retry', self.p.state)
        self.assertIsNone(self.p.final_blocker())

    def test_new_valid_criterion_resets_unresolved_run_once(self):
        self.p.checkpoint('two', [], 'verify', self.criteria)
        self.execute('no JSON'); self.execute('{"wrong":5}')
        self.execute('{"other":5}', [self.spec('other')])
        self.assertEqual(self.p.state['verification_retry']['count'], 1)
        self.execute('{"other":5}', [self.spec('other')])
        self.assertEqual(self.p.state['verification_retry']['count'], 2)


class RebindIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_command_executes_once_and_repair_feedback_reaches_model(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'data.json').write_text('{"values":[2,3]}', encoding='utf-8')
            (root/'probe.py').write_text("import json,pathlib\np=pathlib.Path('count')\np.write_text(str(int(p.read_text())+1) if p.exists() else '1')\nv=json.load(open('data.json'))\nprint(json.dumps({'observations':{'actual':sum(v['values'])}}))\n", encoding='utf-8')
            spec = [{'criterion_id': 'sum', 'artifacts': ['data.json', 'probe.py'], 'comparisons': [
                {'check_id': 'sum', 'pointer': '/wrong', 'expected': 5, 'expected_source': 'formal input'}]}]
            class RepairModel(ScriptedModelClient):
                async def stream(self, request):
                    if len(self.requests) == 2:
                        content = '\n'.join(m.content or '' for m in request.messages if m.role == 'tool')
                        identifier = re.search(r'Saved evidence_id: ([0-9a-f]{32})', content).group(1)
                        self.replies.insert(0, AssistantReply(tool_calls=[ToolInvocation('repair', 'verification_rebind', {
                            'evidence_id': identifier, 'bindings': [{'criterion_id': 'sum', 'check_id': 'sum', 'pointer': '/actual'}]})]))
                    async for event in super().stream(request): yield event
            model = RepairModel([
                AssistantReply(tool_calls=[ToolInvocation('checkpoint', 'task_checkpoint', {
                    'summary': 'sum', 'remaining': [], 'next_action': 'verify', 'criteria': [
                        {'criterion_id': 'sum', 'description': 'sum input', 'kind': 'data', 'check_ids': ['sum']} ]})]),
                AssistantReply(tool_calls=[ToolInvocation('execute', 'bash', {
                    'command': f'"{sys.executable}" -X utf8 probe.py', 'verification': spec})]),
                AssistantReply(content='Sum is 5.')])
            assistant = helpers.RecoveryTests().assistant(model, d)
            events = [e async for e in assistant.run('Sum data.json')]
            self.assertEqual((root/'count').read_text(), '1')
            self.assertEqual(assistant.task_progress.state['status'], 'completed')
            self.assertEqual(assistant.task_progress.state['verified']['sum']['call_id'], 'execute')
            self.assertEqual(len(model.requests), 4)
            content = '\n'.join(m.content or '' for m in model.requests[2].messages if m.role == 'tool')
            self.assertIn('available keys', content)
            self.assertIn('verification_rebind', content)
            content = '\n'.join(m.content or '' for m in model.requests[3].messages if m.role == 'tool')
            self.assertIn('no command was executed', content)
            self.assertEqual(events[-1].message.content, 'Sum is 5.')

    async def test_rebind_rejection_is_visible_and_cannot_change_expected(self):
        with tempfile.TemporaryDirectory() as d:
            assistant = helpers.RecoveryTests().assistant(ScriptedModelClient([]), d)
            result = await assistant.tool_executor.execute(ToolInvocation('bad', 'verification_rebind', {
                'evidence_id': 'unknown', 'bindings': [{'criterion_id': 'a', 'check_id': 'a', 'pointer': '/a'}]}))
            self.assertTrue(result.is_error)
            self.assertTrue(result.details['not_started'])
            self.assertIn('Unknown', result.content)
            self.assertIn('no command', result.content)


if __name__ == '__main__': unittest.main()
