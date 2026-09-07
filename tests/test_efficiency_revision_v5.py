"""Protocol failures and prefix churn seen in v4, without paid model calls."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from dataclasses import asdict

from MiniClaw.coding_agent.assistant.progress import TaskProgress
from MiniClaw.coding_agent.assistant.prompts import BEHAVIOR_PROMPT
from MiniClaw.coding_agent.tools.base import ToolResult
from MiniClaw.coding_agent.tools.bash import BashTool
from MiniClaw.llm.types import ToolInvocation, ChatMessage, AssistantReply
from tests.test_agent_loop import ScriptedModelClient
from tests.test_context_efficiency import pair
from tests.test_memory_cost import make_context
from tests import test_task_recovery as helpers


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.p = TaskProgress(self.root/'.session', self.root)
        self.p.begin_user_turn('Check A and B against formal input')
        self.criteria = [{'criterion_id': k, 'description': k, 'version': 1, 'kind': 'data', 'check_ids': [k]} for k in ['a', 'b']]
        self.p.checkpoint('check', [], 'verify', self.criteria)

    def check(self, key, actual=1):
        return {'criterion_id': key, 'version': 1, 'passed': True, 'evidence': 'executed',
                'comparisons': [{'check_id': key, 'actual': actual, 'expected': 1}]}

    def observe(self, checks, **root):
        text = json.dumps({'task_checks': checks, **root})
        result = ToolResult(text, details={'stdout': text, 'exit_code': 0, 'workspace_changes': {'complete': True, 'changed_paths': []}})
        self.p.observe(ToolInvocation('verify', 'bash', {'task_verification': True}), result)
        return result

    def test_valid_sibling_survives_malformed_or_duplicate_item(self):
        self.observe([self.check('a'), self.check('b')])
        broken = self.check('b'); broken['comparisons'] = []
        self.observe([self.check('a'), broken])
        self.assertEqual(set(self.p.state['verified']), {'a'})
        self.assertIsNotNone(self.p.final_blocker())
        self.observe([self.check('a'), self.check('b'), self.check('b')])
        self.assertEqual(set(self.p.state['verified']), {'a'})
        self.observe([self.check('b')])
        self.assertIsNone(self.p.final_blocker())

    def test_layout_normalization_keeps_real_failures_and_audits_changes(self):
        a = self.check('a', actual=0); comp = a.pop('comparisons')
        result = self.observe([a, self.check('b')], comparisons=comp)
        self.assertEqual(set(self.p.state['verified']), {'b'})
        self.assertEqual(result.details['verification_normalizations'][0]['from'], 'root.comparisons')
        self.assertEqual(self.p.snapshot()['failed_observations']['a'][0]['actual'], 0)
        self.assertIn('actual', self.p.final_blocker())
        a = self.check('a'); comp = a.pop('comparisons')[0]
        a['evidence'] = {'comparisons': {'a': comp}}
        self.observe([a])
        self.assertIsNone(self.p.final_blocker())

    def test_ambiguous_root_or_duplicate_nested_evidence_never_passes(self):
        a = self.check('a')
        self.observe([a], comparisons=copy.deepcopy(a['comparisons']))
        self.assertIsNotNone(self.p.final_blocker())
        self.assertNotIn('a', self.p.state.get('verified', {}))
        a = self.check('a'); a['evidence'] = {'comparisons': {'a': {'check_id': 'wrong', 'actual': 1, 'expected': 1}}}
        self.observe([a])
        self.assertNotIn('a', self.p.state.get('verified', {}))

    def test_same_error_count_is_diagnostic_and_user_turn_resets(self):
        bad = self.check('a'); bad['comparisons'] = []
        for _ in range(3): self.observe([bad])
        self.assertEqual(self.p.state['verification_retry']['count'], 3)
        restored = TaskProgress(self.p.path.parent, self.root)
        self.assertEqual(restored.state['verification_retry']['count'], 3)
        restored.begin_user_turn('Continue fixing the verifier')
        self.assertNotIn('verification_retry', restored.state)

    def test_requirement_edits_need_new_user_revision_not_a_format_retry(self):
        self.observe([self.check('a'), self.check('b')])
        changed = {**self.criteria[0], 'description': 'a new requirement', 'version': 99}
        with self.assertRaises(ValueError): self.p.checkpoint('format repair', [], 'verify', [changed], requirement_revision=1)
        with self.assertRaises(ValueError): self.p.checkpoint('withdraw', [], 'done', [], withdrawn=['a'], requirement_revision=1)
        self.p.checkpoint('reword', [], 'done', [{**self.criteria[0], 'label': 'readable A'}])
        self.assertEqual(set(self.p.state['verified']), {'a', 'b'})
        self.p.begin_user_turn('Replace A with a new requirement')
        self.p.checkpoint('changed', [], 'verify', [changed], requirement_revision=2)
        self.assertEqual(self.p.state['criteria'][0]['version'], 2)
        self.assertEqual(set(self.p.state['verified']), {'b'})
        with self.assertRaises(ValueError): self.p.checkpoint('changed again', [], 'verify', [changed], requirement_revision=2)

    def test_new_ids_assigned_once_and_returned_for_future_binding(self):
        self.p.checkpoint('extra', [], 'verify', [{'description': 'C', 'kind': 'data'}])
        c = self.p.state['criteria'][-1]
        self.assertEqual(c['criterion_id'], 'requirement-3')
        self.assertEqual(c['check_ids'], ['requirement-3'])
        self.p.checkpoint('progress only', [], 'verify', [])
        self.assertEqual(len(self.p.state['criteria']), 3)
        self.p.checkpoint('same requirement', [], 'verify', [{'description': 'C', 'kind': 'data'}])
        self.assertEqual(len(self.p.state['criteria']), 3)
        self.assertEqual(self.p.state['criteria'][-1]['version'], 1)

    def test_structured_missing_observation_does_not_discard_valid_sibling(self):
        specs=[{'criterion_id':k,'artifacts':[], 'comparisons':[
            {'check_id':k,'pointer':'/'+k,'expected':1,'expected_source':'formal input'}]} for k in ['a','b']]
        result=ToolResult('',details={'stdout':'{"observations":{"a":1}}','exit_code':0,
            'workspace_changes':{'complete':True,'changed_paths':[]},
            'verification_plan':self.p.prepare_verification(specs),'verifier_sha256':'captured before execution'})
        self.p.observe(ToolInvocation('test','bash',{'verification':specs}),result)
        self.assertEqual(set(self.p.state['verified']),{'a'})
        self.assertIn('/b',self.p.state['verification_error'])

    def test_prepared_versions_cannot_verify_a_changed_user_requirement(self):
        specs=[{'criterion_id':'a','artifacts':[], 'comparisons':[
            {'check_id':'a','pointer':'/a','expected':1,'expected_source':'formal input'}]}]
        plan=self.p.prepare_verification(specs)
        self.p.begin_user_turn('A must now be two')
        self.p.checkpoint('changed',[],'verify',[{**self.criteria[0],'description':'A is two'}],requirement_revision=2)
        result=ToolResult('',details={'stdout':'{"observations":{"a":1}}','exit_code':0,
            'workspace_changes':{'complete':True,'changed_paths':[]},'verification_plan':plan,'verifier_sha256':'old'})
        self.p.observe(ToolInvocation('test','bash',{'verification':specs}),result)
        self.assertNotIn('a',self.p.state['verified'])
        self.assertIn('version',self.p.state['verification_error'])

    def test_documented_nested_example_and_new_schema_agree(self):
        self.assertNotIn('evidence: string', BEHAVIOR_PROMPT)
        desc = BashTool.input_schema['properties']['task_verification']['description']
        self.assertIn('INSIDE each task_checks item', desc)
        self.assertIn('verification', BashTool.input_schema['properties'])


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_protocol_errors_are_returned_and_model_can_repair_after_three(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'bad_probe.py').write_text("import json\nprint(json.dumps({'task_checks':[{'criterion_id':'a','version':1,'passed':True,'evidence':'actual check'}]}))\n")
            (root/'good_probe.py').write_text("import json\nprint(json.dumps({'task_checks':[{'criterion_id':'a','version':1,'passed':True,'evidence':'actual check','comparisons':[{'check_id':'a','actual':1,'expected':1}]}]}))\n")
            command=f'"{sys.executable}" -X utf8 bad_probe.py'
            replies=[AssistantReply(tool_calls=[ToolInvocation('init','task_checkpoint',{
                'summary':'check','remaining':[],'next_action':'verify',
                'criteria':[{'criterion_id':'a','description':'A','check_ids':['a']} ]})])]
            replies += [AssistantReply(tool_calls=[ToolInvocation(str(i),'bash',{'command':command,'task_verification':True})]) for i in range(5)]
            replies += [AssistantReply(tool_calls=[ToolInvocation('repair','bash',{
                'command':f'"{sys.executable}" -X utf8 good_probe.py','task_verification':True})]), AssistantReply(content='Verified A.')]
            model=ScriptedModelClient(replies)
            assistant=helpers.RecoveryTests().assistant(model,d)
            events=[e async for e in assistant.run('Check A')]
            self.assertEqual(len(model.requests),8)
            self.assertEqual(assistant.loop.max_turns,32)
            self.assertEqual(assistant.task_progress.state['status'],'completed')
            self.assertFalse(assistant.task_progress.pending())
            self.assertFalse(any((e.details or {}).get('pause_reason') == 'verification_stalled' for e in events))
            for request in model.requests[2:7]:
                self.assertTrue(any('missing comparison IDs' in (m.content or '') for m in request.messages if m.role == 'tool'))

    async def test_observation_command_runs_once_versions_bound_and_wrong_reply_repaired(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'data.json').write_text('{"values":[2,3]}')
            (root/'verify_probe.py').write_text("import json,pathlib\np=pathlib.Path('count')\np.write_text(str(int(p.read_text())+1) if p.exists() else '1')\nv=json.load(open('data.json'))\nprint(json.dumps({'observations':{'total':sum(v['values'])}}))\n")
            spec = [{'criterion_id': 'requirement-1', 'artifacts': ['data.json','verify_probe.py'], 'comparisons': [
                {'check_id': 'requirement-1', 'pointer': '/total', 'expected': 5, 'expected_source': 'formal input values 2+3', 'final_pointer': '/total'}]}]
            model = ScriptedModelClient([
                AssistantReply(tool_calls=[ToolInvocation('checkpoint','task_checkpoint', {'summary':'sum','remaining':[], 'next_action':'verify','criteria':[{'description':'sum formal input','kind':'data'}]})]),
                AssistantReply(tool_calls=[ToolInvocation('verify','bash', {'command':f'"{sys.executable}" -X utf8 verify_probe.py','verification':spec})]),
                AssistantReply(content='{"total":4}'), AssistantReply(content='{"total":5}')])
            assistant = helpers.RecoveryTests().assistant(model,d)
            events = [e async for e in assistant.run('Sum data.json; return total as JSON')]
            self.assertEqual((root/'count').read_text(), '1')
            self.assertEqual(events[-1].message.content, '{"total":5}')
            self.assertEqual(assistant.task_progress.state['status'], 'completed')
            self.assertEqual(assistant.task_progress.state['verified']['requirement-1']['version'], 1)
            self.assertEqual(len(model.requests), 4)
            self.assertEqual(model.requests[2].tools, [])

    async def test_invalid_plan_rejected_before_command_side_effect(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            assistant = helpers.RecoveryTests().assistant(ScriptedModelClient([]),d)
            assistant.task_progress.checkpoint('x', [], 'verify', [{'criterion_id':'x','description':'x','version':1,'check_ids':['needed']}])
            assistant.task_progress.state['verified']={'x':{'version':1,'artifacts':{}}}
            result = await assistant.tool_executor.execute(ToolInvocation('bad','bash',{
                'command':f'"{sys.executable}" -c "open(\'side-effect\',\'w\').write(\'x\')"',
                'verification':[{'criterion_id':'x','artifacts':[], 'comparisons':[{'check_id':'wrong','pointer':'/x','expected':1,'expected_source':'input'}]}]}))
            self.assertTrue(result.is_error)
            self.assertFalse((root/'side-effect').exists())
            self.assertTrue(result.details['not_started'])
            self.assertIn('x',assistant.task_progress.state['verified'])
            self.assertIsNotNone(assistant.task_progress.final_blocker())


class HistoryTests(unittest.TestCase):
    def test_small_append_keeps_prefix_restart_and_large_append_rebases_in_batch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); context = make_context(root)
            raw = [ChatMessage(role='user',content='implement')]
            for i in range(5): raw += pair(str(i),'ok','write',{'path':str(i),'content':'x'*1100})
            first = context.transform_request_context(raw)
            raw += pair('extra','ok','write',{'path':'extra','content':'y'*1100})
            second = context.transform_request_context(raw)
            self.assertEqual(second[:len(first)], first)
            self.assertFalse(context.last_projection['history_rebase'])
            restored = make_context(root)
            self.assertEqual(restored.transform_request_context(raw), second)
            for i in range(8): raw += pair('more'+str(i),'ok','write',{'path':str(i),'content':'z'*1300})
            smaller = restored.transform_request_context(raw)
            self.assertTrue(restored.last_projection['history_rebase'])
            self.assertGreater(restored.last_projection['closed_tool_pairs_archived'], 1)
            self.assertTrue(any('context artifact' in json.dumps(asdict(m)) for m in smaller))
            self.assertNotIn('context artifact', json.dumps([asdict(m) for m in raw]))
            raw += pair('last','ok','bash',{'command':'true'})
            stable = restored.transform_request_context(raw)
            self.assertEqual(stable[:len(smaller)], smaller)

    def test_real_user_boundary_allows_rebase_but_completion_feedback_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            context = make_context(Path(d))
            raw = [ChatMessage(role='user',content='task'), *pair('r','read body')]
            first = context.transform_request_context(raw)
            raw.append(ChatMessage(role='user',content='[COMPLETION_CHECK] fix verification'))
            second = context.transform_request_context(raw)
            self.assertEqual(second[:len(first)], first)
            self.assertEqual(context.last_projection['stable_history_messages'], len(first))
            raw.append(ChatMessage(role='user',content='new real requirement'))
            context.transform_request_context(raw)
            self.assertEqual(context.last_projection['stable_history_messages'], 0)
