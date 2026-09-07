"""Registered checks, strict evidence and additive error categories."""
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from MiniClaw.coding_agent.assistant.progress import TaskProgress
from MiniClaw.coding_agent.tools.base import ToolResult
from MiniClaw.llm.types import ToolInvocation
from MiniClaw.evaluation.verification_metrics import verification_counters
from MiniClaw.coding_agent.runtime.execution import VERIFICATION_HELPERS


class RegisteredChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'result.json').write_text('{}')
        self.p = TaskProgress(self.root/'.session', self.root)
        self.p.begin_user_turn('Check the result')
        self.criterion = {'criterion_id':'c', 'description':'value is true', 'kind':'data',
            'artifacts':['result.json'], 'comparisons':[
                {'check_id':'ok', 'expected':True, 'expected_source':'user requirement'}]}
        self.p.checkpoint('check', [], 'verify', [self.criterion])

    def observe(self, text, exit_code=0, **details):
        spec = [{'criterion_id':'c'}]
        result = ToolResult(text, details={'stdout':text, 'exit_code':exit_code,
            'verification_plan':self.p.prepare_verification(spec), 'verifier_sha256':'test', **details})
        self.p.observe(ToolInvocation('v','bash',{'verification':spec}), result)
        return result.details['verification_current']

    def test_register_once_resolve_repeat_and_invalidate(self):
        self.assertIsNone(self.observe('{"observations":{"ok":true}}')['error'])
        self.assertFalse(self.p.pending())
        self.assertIsNone(self.observe('{"ok":true}')['error'])
        (self.root/'result.json').write_text('{"changed":1}')
        self.assertTrue(self.p.pending())
        restored = TaskProgress(self.root/'.session', self.root)
        self.assertEqual(restored.prepare_verification([{'criterion_id':'c'}])['checks'][0]['comparisons'][0]['expected'], True)

    def test_registered_expectations_cannot_be_changed_by_bool_int_alias(self):
        changed = copy.deepcopy(self.criterion); changed['comparisons'][0]['expected'] = 1
        with self.assertRaises(ValueError):
            self.p.checkpoint('repair', [], 'verify', [changed])
        with self.assertRaises(ValueError):
            self.p.prepare_verification([{'criterion_id':'c', 'comparisons':changed['comparisons']}])
        with self.assertRaises(ValueError):
            self.p.prepare_verification([{'criterion_id':'c', 'artifacts':[]}])

    def test_binding_correction_keeps_expectation(self):
        comparisons = copy.deepcopy(self.criterion['comparisons'])
        comparisons[0]['pointer'] = '/nested/ok'
        plan = self.p.prepare_verification([{'criterion_id':'c','comparisons':comparisons}])
        self.assertEqual(plan['checks'][0]['comparisons'][0]['pointer'], '/nested/ok')

    def test_legacy_cannot_bypass_registered_expectation(self):
        payload = json.dumps({'task_checks':[{'criterion_id':'c', 'version':1, 'passed':True,
            'evidence':'executed', 'artifacts':['result.json'], 'comparisons':[
                {'check_id':'ok', 'actual':False, 'expected':False}]}]})
        result = ToolResult(payload, details={'stdout':payload,'exit_code':0})
        self.p.observe(ToolInvocation('legacy','bash',{'task_verification':True}), result)
        self.assertIn('Registered expectations', result.details['verification_current']['error'])
        self.assertTrue(self.p.pending())

    def test_error_categories_do_not_make_wrong_values_pass(self):
        self.assertEqual(self.observe('CHECK_OK')['error_category'], 'protocol_error')
        self.assertEqual(self.observe('{"ok":true}', 7)['error_category'], 'execution_error')
        self.assertEqual(self.observe('', None, not_started=True, verification_plan_error='missing field')['error_category'], 'plan_error')
        result = self.observe('{"ok":false}')
        self.assertIsNone(result['error'])
        self.assertTrue(result['failed_observations'])
        self.assertTrue(self.p.final_blocker())
        self.assertNotEqual(self.p.state['status'], 'paused')


class FeedbackAndHelper(unittest.TestCase):
    def test_report_aggregates_current_categories_separately_from_pending(self):
        from MiniClaw.evaluation.runner import _aggregate_metrics, ADDITIVE_METRICS
        metrics = _aggregate_metrics({'main':[{'type':'tool.call','data':{
            'status':'error','tool_name':'bash','details':{'task_verification':{
                'feedback_version':3,'status':'execution_error','error':'exit 1','pending_errors':{'old':'bad'}}}}}]})
        self.assertEqual(metrics['verification_execution_error_feedback'], 1)
        self.assertEqual(metrics['verification_error_feedback'], 1)
        self.assertEqual(metrics['verification_pending_feedback'], 1)
        self.assertEqual(metrics['verification_protocol_error_feedback'], 0)
        self.assertIn('verification_execution_error_feedback', ADDITIVE_METRICS)

    def test_history_net_cost_accounts_for_replay_and_invalidates_changed_files(self):
        import hashlib
        from tests.test_efficiency_revision_v5 import make_context
        from MiniClaw.llm.types import ChatMessage
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); context = make_context(root)
            path = root/'a.py'; path.write_text('print(123)')
            call = ToolInvocation('read1','read',{'path':'a.py'})
            context.read_snapshots.observe(call, ToolResult('print(123)', details={
                'path':str(path),'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}))
            raw = [ChatMessage(role='user',content='continue after summary')]
            context.transform_request_context(raw)
            cost = context.last_projection
            self.assertGreater(cost['read_replay_added_tokens'], 0)
            self.assertEqual(cost['history_net_added_tokens'],
                cost['read_replay_added_tokens'] - cost['history_projection_saved_tokens'])
            self.assertEqual(cost['request_history_tokens'] - cost['raw_history_tokens'], cost['history_net_added_tokens'])
            path.write_text('changed')
            context.transform_request_context(raw)
            self.assertEqual(context.last_projection['read_replay_added_tokens'], 0)

    def test_legacy_feedback_is_not_guessed(self):
        counts = verification_counters({'task_verification':{'error':'git failed', 'feedback_version':2}})
        self.assertEqual(counts, {'verification_error_feedback':1, 'verification_legacy_unclassified_feedback':1})
        counts = verification_counters({'task_verification':{'feedback_version':3,
            'status':'checks_processed', 'pending_errors':{'old':'bad'}}})
        self.assertEqual(counts, {'verification_pending_feedback':1})
        counts = verification_counters({'task_verification':{'feedback_version':3,
            'status':'execution_error', 'error':'nonzero'}})
        self.assertEqual(counts['verification_execution_error_feedback'], 1)

    def test_helper_serializes_actual_values_once_without_coercion(self):
        spec = importlib.util.spec_from_file_location('probe_helper', VERIFICATION_HELPERS/'miniclaw_verification.py')
        helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
        with self.assertRaises(ValueError): helper.emit({'bad':float('nan')})
        output = io.StringIO()
        with redirect_stdout(output): helper.emit({'ok':False, 'value':2})
        self.assertEqual(json.loads(output.getvalue()), {'observations':{'ok':False, 'value':2}})
        with self.assertRaises(ValueError): helper.emit({'ok':True})
