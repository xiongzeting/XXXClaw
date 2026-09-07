import contextlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

from MiniClaw.coding_agent.assistant.acceptance import compare,pointer
from MiniClaw.coding_agent.assistant.progress import TaskProgress
from MiniClaw.coding_agent.tools.base import ToolResult
from MiniClaw.llm.types import ToolInvocation
from MiniClaw.evaluation.faults import probe_recovery
from MiniClaw.llm.types import AssistantReply
from MiniClaw.trace.store import read_trace_records
from tests.test_agent_loop import ScriptedModelClient
from tests import test_task_recovery as recovery_helpers


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.progress=TaskProgress(self.root/'.state',self.root)
        self.criteria=[{'criterion_id':'data','description':'derived fields','version':1,'kind':'data','check_ids':['net','ids']},
                       {'criterion_id':'other','description':'other behavior','version':1}]
        self.progress.checkpoint('build',[],'verify',self.criteria)

    def observe(self,checks):
        text=json.dumps({'task_checks':checks})
        self.progress.observe(ToolInvocation('test','bash',{'task_verification':True}),
            ToolResult(text,details={'stdout':text,'exit_code':0,'workspace_changes':{'complete':True,'changed_paths':[]}}))

    def data(self):
        return {'criterion_id':'data','version':1,'passed':True,'evidence':'derived from input',
                'comparisons':[{'check_id':'net','actual':55,'expected':55},
                               {'check_id':'ids','actual':['A','B'],'expected':['A','B'],'final_pointer':'/ids'}]}

    def other(self):
        return {'criterion_id':'other','version':1,'passed':True,'evidence':'executed'}

    def test_correct_total_does_not_hide_missing_member_or_allow_claimed_pass(self):
        item=self.data();item['comparisons'][1]['actual']=['B']
        self.observe([item,self.other()])
        self.assertEqual(self.progress.pending(),['derived fields'])
        self.assertIsNotNone(self.progress.final_blocker())

    def test_required_fields_cannot_be_skipped_or_replaced_by_duplicate(self):
        for comparisons in [[],self.data()['comparisons'][:1],self.data()['comparisons'][:1]*2]:
            item=self.data();item['comparisons']=comparisons
            self.observe([item]);self.assertIsNotNone(self.progress.final_blocker())

    def test_failed_recheck_preserves_other_proof_and_does_not_resurrect_failed_id(self):
        self.observe([self.data(),self.other()])
        self.assertIsNone(self.progress.final_blocker())
        broken=self.data();broken['comparisons']=[None]
        self.observe([broken])
        self.assertIn('other',self.progress.state['verified'])
        self.assertNotIn('data',self.progress.state['verified'])
        self.observe([self.other()])
        self.assertIsNotNone(self.progress.final_blocker())

    def test_runtime_reads_actual_json_and_tracks_file_version(self):
        p=self.root/'result.json';p.write_text('{"net":0,"ids":["A","B"]}')
        item=self.data();item['comparisons'][0]={'check_id':'net','actual_file':'result.json','pointer':'/net','expected':55}
        self.observe([item,self.other()]);self.assertIsNotNone(self.progress.final_blocker())
        p.write_text('{"net":55,"ids":["A","B"]}')
        self.observe([item]);self.assertIsNone(self.progress.final_blocker())
        p.write_text('{"net":0}')
        self.assertIsNotNone(self.progress.final_blocker())

    def test_file_comparisons_share_runtime_path_and_credential_protection(self):
        (self.root/'result.json').write_text('{"net":55}')
        for path in ['result.json','/workspace/result.json',str(self.root/'result.json')]:
            item=self.data();item['comparisons'][0]={'check_id':'net','actual_file':path,'pointer':'/net','expected':55}
            self.observe([item,self.other()])
            self.assertIsNone(self.progress.final_blocker())
        (self.root/'secrets.json').write_text('{"net":55}')
        for path in ['secrets.json','../outside.json','.aster/session.json']:
            item=self.data();item['comparisons'][0]={'check_id':'net','actual_file':path,'pointer':'/net','expected':55}
            self.observe([item])
            self.assertNotIn('data',self.progress.state['verified'])
            self.assertIsNotNone(self.progress.final_blocker())

    def test_final_reply_cannot_change_verified_ids(self):
        self.observe([self.data(),self.other()])
        self.assertIsNotNone(self.progress.final_response_blocker('{"ids":["A"]}'))
        self.assertIsNone(self.progress.final_response_blocker('```json\n{"ids":["A","B"]}\n```'))

    def test_invalid_final_pointer_is_rejected_during_verification(self):
        for path in [None, 'ids', '/bad~2field']:
            item=self.data();item['comparisons'][1]['final_pointer']=path
            self.observe([item,self.other()])
            self.assertNotIn('data',self.progress.state['verified'])
            self.assertIn('Invalid verification',self.progress.state['verification_error'])

    def test_invalid_command_leaves_actionable_resume_step(self):
        self.observe([self.data(),self.other()])
        self.progress.observe(ToolInvocation('bad','bash',{'task_verification':True}),
            ToolResult('broken',is_error=True,details={'exit_code':1,
                'workspace_changes':{'complete':True,'changed_paths':[]}}))
        self.assertEqual(self.progress.pending(),[])
        self.progress.finish('stop',[])
        self.assertIn('验证命令',self.progress.state['next_action'])

    def test_changing_required_check_ids_needs_new_version(self):
        item={**self.criteria[0],'check_ids':['net']}
        with self.assertRaises(ValueError): self.progress.checkpoint('changed',[],'verify',[item])
        self.progress.begin_user_turn('Change the required comparisons')
        self.progress.checkpoint('changed',[],'verify',[{**item,'version':2}],requirement_revision=1)

    def test_unverified_finish_is_persisted_as_resumable_pause(self):
        self.assertEqual(self.progress.finish('stop',[]),'verification_incomplete')
        restored=TaskProgress(self.progress.path.parent,self.root)
        self.assertEqual(restored.state['status'],'paused')
        self.assertIsNotNone(restored.final_blocker())

    def test_types_order_multiplicity_and_pointers(self):
        self.assertFalse(compare(True,1));self.assertFalse(compare([1],[1.0]))
        self.assertFalse(compare(['x','x'],['x'],'same_members'))
        self.assertFalse(compare(['b','a'],['a','b']))
        self.assertTrue(compare(['b','a'],['a','b'],'same_members'))
        self.assertEqual(pointer({'a/b':{'~key':[3]}},'/a~1b/~0key/0'),3)
        for value in [float('nan'),float('inf')]:
            with self.assertRaises(ValueError):compare(value,value)
        with self.assertRaises(ValueError):pointer([1],'/01')

    def test_recovery_requires_all_stages_as_well_as_actual_comparisons(self):
        criterion={'criterion_id':'recovery','description':'retry once','version':1,'kind':'recovery','check_ids':['state'],
                   'required_scenarios':['before_commit','during_commit','response_lost','retry']}
        self.progress.checkpoint('recovery',[],'verify',[criterion])
        result={'criterion_id':'recovery','version':1,'passed':True,'evidence':'fault probes',
                'comparisons':[{'check_id':'state','actual':{'n':1},'expected':{'n':1}}]}
        self.observe([result]);self.assertNotIn('recovery',self.progress.state['verified'])
        result['scenarios']=[{'stage':s,'injected':True,'observed':True} for s in ['before_commit','during_commit','response_lost','retry']]
        self.observe([result]);self.assertIn('recovery',self.progress.state['verified'])


class RecoveryProbeTests(unittest.TestCase):
    def factory(self,rollback):
        @contextlib.contextmanager
        def build():
            with tempfile.TemporaryDirectory() as d:
                p=Path(d);a=p/'stock';b=p/'requests'
                a.write_text('10');b.write_text('[]')
                def snapshot():return {'stock':json.loads(a.read_text()),'requests':json.loads(b.read_text())}
                def operation():
                    if 'r' in json.loads(b.read_text()):return
                    before=(a.read_bytes(),b.read_bytes())
                    try:
                        for target,value in [(a,json.loads(a.read_text())-1),(b,['r'])]:
                            tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(value));os.replace(tmp,target)
                    except OSError:
                        if rollback:a.write_bytes(before[0]);b.write_bytes(before[1])
                        raise
                yield operation,snapshot
        return build

    def test_generic_probe_rejects_partial_commits_and_accepts_rollback(self):
        bad=probe_recovery(self.factory(False));good=probe_recovery(self.factory(True))
        self.assertEqual(bad['observed_commits'],2)
        self.assertTrue(bad['covered']);self.assertFalse(bad['passed'])
        self.assertTrue(good['covered']);self.assertTrue(good['passed'])
        self.assertEqual(len(good['probes']),4)

    def test_retry_exception_is_reported_as_failure(self):
        @contextlib.contextmanager
        def factory():
            with self.factory(True)() as (operation,snapshot):
                def fail_duplicate():
                    if snapshot()['requests']:
                        raise RuntimeError('duplicate request rejected')
                    operation()
                yield fail_duplicate,snapshot
        result=probe_recovery(factory)
        self.assertTrue(result['covered'])
        self.assertFalse(result['passed'])
        self.assertFalse(result['response_lost_retry_exactly_once'])
        self.assertIn('duplicate request rejected',result['response_lost_error'])


class AcceptanceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_verification_command_then_corrected_reply_does_not_replay_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'input.json').write_text('[{"id":"K2","amount":8},{"id":"K1","amount":3}]')
            (root/'result.json').write_text('{"total":11,"ids":["K1","K2"]}')
            (root/'verify.py').write_text('''import json
from pathlib import Path
rows=json.loads(Path('input.json').read_text())
expected={'total':sum(r['amount'] for r in rows),'ids':sorted(r['id'] for r in rows)}
print(json.dumps({'task_checks':[{'criterion_id':'result','version':1,'passed':True,
 'evidence':'sum and IDs derived from input.json','artifacts':['input.json','verify.py'],
 'comparisons':[{'check_id':k,'actual_file':'result.json','pointer':'/'+k,
 'expected':v,'final_pointer':'/'+k} for k,v in expected.items()]}]}))
''',encoding='utf-8')
            args=[sys.executable,'-X','utf8','verify.py']
            command=subprocess.list2cmdline(args) if os.name=='nt' else shlex.join(args)
            wrong='{"total":11,"ids":["K2"]}'
            correct='{"total":11,"ids":["K1","K2"]}'
            model=ScriptedModelClient([
                AssistantReply(tool_calls=[ToolInvocation('plan','task_checkpoint',{
                    'summary':'verify output','remaining':[],'next_action':'verify',
                    'criteria':[{'criterion_id':'result','description':'all derived output fields',
                        'version':1,'kind':'data','check_ids':['total','ids']}]} )]),
                AssistantReply(tool_calls=[ToolInvocation('verify','bash',{
                    'command':command,'task_verification':True})]),
                AssistantReply(content=wrong),AssistantReply(content=correct)])
            assistant=recovery_helpers.RecoveryTests().assistant(model,directory)
            self.assertEqual(assistant.loop.max_turns,32)
            events=[event async for event in assistant.run('Verify result.json and return its exact JSON.')]
            self.assertEqual(events[-1].message.content,correct)
            self.assertEqual(sum(e.type=='tool_started' for e in events),2)
            self.assertFalse(any(e.text==wrong or (e.message and e.message.role=='assistant' and e.message.content==wrong) for e in events))
            self.assertEqual(assistant.task_progress.state['status'],'completed')
            self.assertEqual(assistant.run_state_store.read().status,'completed')
            self.assertEqual(len(model.requests),4)
            self.assertEqual(model.requests[2].tools, [])
            self.assertTrue(model.requests[3].tools)  # Invalid final JSON reopens repair.

    async def test_incomplete_finalization_stays_paused_in_trace_and_ui(self):
        with tempfile.TemporaryDirectory() as directory:
            model=ScriptedModelClient([AssistantReply(content='candidate completion')])
            assistant=recovery_helpers.RecoveryTests().assistant(model,directory)
            assistant.task_progress.checkpoint('work',[],'verify',[
                {'criterion_id':'missing','description':'required check','version':1}])
            # Exercise the finalization backstop even if an upstream guard is absent.
            assistant.loop.final_guard=None
            events=[event async for event in assistant.run('Finish the task')]
            self.assertEqual(events[-1].details['pause_reason'],'verification_incomplete')
            self.assertEqual(assistant.run_state_store.read().status,'paused')
            self.assertEqual(assistant.task_progress.state['status'],'paused')
            records=read_trace_records(Path(directory,'.aster','trace.jsonl'))
            completed=[r['data'] for r in records if r['type']=='run.completed'][-1]
            self.assertEqual(completed['status'],'paused')
            self.assertEqual(completed['pause_reason'],'verification_incomplete')


if __name__=='__main__':unittest.main()
