"""Acceptance scope, aliases, and current-versus-pending error feedback."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from MiniClaw.coding_agent.assistant.progress import TaskProgress
from MiniClaw.coding_agent.tools.base import ToolResult
from MiniClaw.llm.types import ToolInvocation
from tests.test_agent_loop import ScriptedModelClient
from tests import test_task_recovery as helpers


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.p = TaskProgress(self.root/'.session', self.root)
        self.p.begin_user_turn('Check file scope and explicitly requested retry behavior')
        self.a = {'criterion_id':'scope', 'description':'Only modify allowed files', 'version':1,
                  'kind':'recovery', 'check_ids':['allowed']}
        self.b = {'criterion_id':'transaction', 'description':'Retry after a lost response once', 'version':1,
                  'kind':'recovery', 'check_ids':['once'], 'required_scenarios':['response_lost','retry']}
        self.p.checkpoint('verify', [], 'test', [self.a, self.b])

    def spec(self, key='scope', check='allowed'):
        return {'criterion_id':key, 'artifacts':[], 'comparisons':[
            {'check_id':check, 'expected':True, 'expected_source':'task requirements'}]}

    def observe(self, text, specs=None):
        args = {'task_verification':True} if specs is None else {'verification':specs}
        details = {'stdout':text, 'exit_code':0, 'workspace_changes':{'complete':True,'changed_paths':[]}}
        if specs is not None:
            details.update(verification_plan=self.p.prepare_verification(specs), verifier_sha256='command')
        result = ToolResult(text, details=details)
        self.p.observe(ToolInvocation('verify','bash',args), result)
        return result

    def test_category_does_not_invent_four_fault_requirements(self):
        result = self.observe('{"allowed":true}', [self.spec()])
        self.assertIsNone(result.details['verification_current']['error'])
        self.assertIn('scope', self.p.state['verified'])
        restored = TaskProgress(self.p.path.parent, self.root)
        self.assertEqual(restored.state['criteria'][0]['required_scenarios'], [])

    def test_explicit_scenarios_work_in_structured_interface(self):
        spec = [self.spec('transaction','once')]
        result = self.observe('{"once":true}', spec)
        self.assertIn('Missing executed scenarios', result.details['verification_current']['error'])
        self.assertNotIn('transaction', self.p.state['verified'])
        scenarios = [{'stage':s, 'injected':True, 'observed':True} for s in ['response_lost','retry']]
        self.observe(json.dumps({'observations':{'once':True, 'scenarios':{'transaction':scenarios}}}), spec)
        self.assertIn('transaction', self.p.state['verified'])

    def test_scenario_flags_and_actual_values_remain_strict(self):
        for bad in ['true', False, 1]:
            value = {'once':True, 'scenarios':{'transaction':[
                {'stage':'response_lost','injected':bad,'observed':True},
                {'stage':'retry','injected':True,'observed':True}]}}
            self.observe(json.dumps(value), [self.spec('transaction','once')])
            self.assertNotIn('transaction', self.p.state['verified'])
        value['scenarios']['transaction'][0]['injected'] = True
        value['once'] = False
        self.observe(json.dumps(value), [self.spec('transaction','once')])
        self.assertNotIn('transaction', self.p.state['verified'])
        self.assertIn('transaction', self.p.state['failed_checks'])

    def test_legacy_alias_normalized_and_ambiguous_alias_keeps_good_sibling(self):
        transaction = {'criterion_id':'transaction','version':1,'passed':True,'evidence':'executed faults',
            'comparisons':[{'check_id':'once','actual':True,'expected':True}],
            'recovery':[{'stage':s,'injected':True,'observed':True} for s in ['response_lost','retry']]}
        result = self.observe(json.dumps({'task_checks':[transaction]}))
        self.assertIn('transaction', self.p.state['verified'])
        self.assertEqual(result.details['verification_normalizations'][0]['to'], 'scenarios')
        transaction['scenarios'] = copy.deepcopy(transaction['recovery'])
        scope = {'criterion_id':'scope','version':1,'passed':True,'evidence':'executed scope assertion',
                 'comparisons':[{'check_id':'allowed','actual':True,'expected':True}]}
        result = self.observe(json.dumps({'task_checks':[scope, transaction]}))
        self.assertIn('Ambiguous', result.details['verification_current']['error'])
        self.assertIn('scope', self.p.state['verified'])
        self.assertNotIn('transaction', self.p.state['verified'])

    def test_explicit_scenarios_cannot_be_withdrawn_as_format_repair(self):
        changed = {**self.b, 'required_scenarios':[]}
        with self.assertRaisesRegex(ValueError, 'newer user message'):
            self.p.checkpoint('repair', [], 'verify', [changed], requirement_revision=1)
        self.p.checkpoint('same', [], 'verify', [{'criterion_id':'transaction','description':self.b['description']}])
        self.assertEqual(self.p.state['criteria'][1]['required_scenarios'], ['response_lost','retry'])

    def test_old_persisted_recovery_contract_is_not_silently_weakened(self):
        old = copy.deepcopy(self.p.state)
        old['criteria'][1].pop('required_scenarios')
        self.p.path.write_text(json.dumps(old), encoding='utf-8')
        restored = TaskProgress(self.p.path.parent, self.root)
        self.assertEqual(restored.state['criteria'][1]['required_scenarios'],
                         ['before_commit','during_commit','response_lost','retry'])

    def test_check_ids_compatibility_does_not_drop_or_add_checks(self):
        spec = self.spec(); spec['check_ids'] = ['allowed']
        self.assertIn('binding_normalizations', self.p.prepare_verification([spec])['checks'][0])
        for ids in [[], ['wrong'], ['allowed','allowed']]:
            spec['check_ids'] = ids
            with self.assertRaisesRegex(ValueError, 'comparisons'):
                self.p.prepare_verification([spec])

    def test_known_preflight_error_clears_when_that_check_is_repaired(self):
        spec = self.spec()
        self.p.observe(ToolInvocation('bad','bash',{'verification':[spec]}),
            ToolResult('ValueError: missing expected', is_error=True, details={'not_started':True}))
        self.assertEqual(set(self.p.state['verification_errors']), {'scope'})
        result = self.observe('{"allowed":true}', [spec])
        self.assertIsNone(result.details['verification_current']['error'])
        self.assertFalse(self.p.state['verification_errors'])
        self.assertTrue(self.p.pending())  # transaction still needs its own evidence

    def test_successful_sibling_reports_old_errors_separately(self):
        self.observe('{"wrong":true}', [self.spec('transaction','once')])
        result = self.observe('{"allowed":true}', [self.spec()])
        current = result.details['verification_current']
        self.assertIsNone(current['error'])
        self.assertIn('transaction', current['pending_errors'])
        self.assertIsNotNone(self.p.final_blocker())


class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_error_and_pending_history_are_visible_but_separate(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            assistant = helpers.RecoveryTests().assistant(ScriptedModelClient([]), d)
            p = assistant.task_progress
            p.begin_user_turn('Verify A and B')
            p.checkpoint('verify', [], 'test', [{'criterion_id':k,'description':k,'version':1,
                'kind':'data','check_ids':[k]} for k in ['a','b']])
            spec = lambda k: {'criterion_id':k,'artifacts':[], 'comparisons':[
                {'check_id':k,'expected':True,'expected_source':'input'}]}
            (root/'probe.py').write_text('print(\'{"a":true}\')', encoding='utf-8')
            command = f'"{sys.executable}" -X utf8 probe.py'
            bad = {**spec('b'), 'unexpected':[]}
            result = await assistant.tool_executor.execute(ToolInvocation('bad','bash',{'command':command,'verification':[bad]}))
            self.assertTrue(result.details['not_started'])
            self.assertIn('unexpected arguments', result.details['task_verification']['error'])
            self.assertNotIn('did not exit successfully', result.details['task_verification']['error'])
            result = await assistant.tool_executor.execute(ToolInvocation('good','bash',{'command':command,'verification':[spec('a')]}))
            self.assertFalse(result.is_error)
            self.assertIsNone(result.details['task_verification']['error'])
            self.assertIn('b', result.details['task_verification']['pending_errors'])
            self.assertIn('not new failures', result.content)
            self.assertIsNotNone(p.final_blocker())
            (root/'probe.py').write_text('print(\'{"b":true}\')', encoding='utf-8')
            b = spec('b'); b['check_ids'] = ['b']
            result = await assistant.tool_executor.execute(ToolInvocation('repair','bash',{'command':command,'verification':[b]}))
            self.assertFalse(result.is_error)
            self.assertIsNone(p.final_blocker())
