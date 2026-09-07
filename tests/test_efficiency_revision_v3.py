"""Offline regressions for observed repeated context and verification transport failures."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from MiniClaw.coding_agent.assistant.verification import decode_verification
from MiniClaw.coding_agent.memory.manager import MemoryManager
from MiniClaw.coding_agent.memory.working import CHECKPOINT_MARKER
from MiniClaw.coding_agent.tools.base import ToolResult
from MiniClaw.llm.types import AssistantReply, ChatMessage, ModelProfile, ToolInvocation
from MiniClaw.evaluation.runner import _run_phase
from tests.test_context_efficiency import pair
from tests.test_memory_cost import make_context, SummaryClient
from tests.test_agent_loop import ScriptedModelClient
from tests import test_task_recovery as recovery_helpers


class TransportTests(unittest.TestCase):
    def record(self, **changes):
        return json.dumps({'task_checks':[{'criterion_id':'c','version':1,'passed':True,
                          'evidence':{'actual':3,'expected':3}, **changes}]})

    def test_stdout_stderr_logs_and_object_evidence(self):
        for source in ('stdout','stderr'):
            with self.subTest(source=source):
                result=ToolResult('',details={'stdout':'', 'stderr':'', source:'log\n'+self.record()+'\nSMOKE_OK\n'})
                payload, actual_source=decode_verification(result)
                self.assertEqual(actual_source,source)
                self.assertEqual(payload['task_checks'][0]['evidence']['actual'],3)

    def test_reject_ambiguous_truncated_nested_and_malformed_records(self):
        record=self.record()
        bad=[record+'\n'+record, '[\n'+record+'\n]', '{"wrapper":'+record+'}',
             '{\nBROKEN,\n'+record+'\n}', record+'SMOKE_OK',
             '{"task_checks":[],"task_checks":[]}', '{"task_checks":NaN}']
        for body in bad:
            with self.subTest(body=body), self.assertRaises(ValueError):
                decode_verification(ToolResult(body))
        with self.assertRaises(ValueError):
            decode_verification(ToolResult('',details={'stdout':record,'stderr':record}))
        with self.assertRaises(ValueError):
            decode_verification(ToolResult(record,details={'capture_truncated':True}))


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cumulative_small_calls_shrink_preserving_fresh_failure_pending_and_originals(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); context=make_context(root,target_tokens=8192)
            messages=[ChatMessage(role='user',content='Withdraw V1; use V2. Preserve on error.')]
            for i in range(20):
                messages.extend(pair(str(i),str(i)+' result '*220,'bash',{'command':str(i)+' code '*320}))
            failure=pair('failed','failure detail '*300,'bash',{'command':'failed command '*200})
            await context.artifactize_live_result(failure[0].tool_calls[0],ToolResult(failure[1].content,is_error=True))
            messages.extend(failure)
            messages.append(ChatMessage(role='assistant',tool_calls=[ToolInvocation('pending','write',{'path':'new.py','content':'pending '*400})]))
            messages.extend(pair('fresh','fresh code '*600))
            original=json.dumps([m.content for m in messages])
            dry=context.transform_request_context(messages,persist=False)
            self.assertFalse(context.artifacts.root.exists())
            view=context.transform_request_context(messages)
            self.assertEqual([m.content for m in dry],[m.content for m in view])
            self.assertLess(context.last_projection['closed_tool_bytes_after'],context.last_projection['closed_tool_bytes_before']/2)
            self.assertEqual(view[-1].content,messages[-1].content)
            self.assertEqual(view[-3].tool_calls[0].arguments,messages[-3].tool_calls[0].arguments)
            self.assertEqual(view[-4].content,messages[-4].content)
            self.assertEqual(json.dumps([m.content for m in messages]),original)
            saved=[json.loads(p.read_text(encoding='utf-8')) for p in context.artifacts.root.glob('closed-tool-pair-*.json')]
            self.assertTrue(any(v['call']['arguments']['command']==messages[1].tool_calls[0].arguments['command'] and
                                v['result']['content']==messages[2].content for v in saved))
            self.assertEqual([c.call_id for m in view for c in m.tool_calls], [c.call_id for m in messages for c in m.tool_calls])

    def test_old_system_checkpoint_is_low_trust_and_does_not_become_user_request(self):
        with tempfile.TemporaryDirectory() as d:
            c=make_context(Path(d))
            old=ChatMessage(role='system',content=CHECKPOINT_MARKER+'\nOld pending: recheck everything')
            view=c.transform_request_context([ChatMessage(role='user',content='Use V2, V1 withdrawn.'),old])
            self.assertEqual(view[-1].role,'assistant')
            self.assertEqual(c._latest_user_request(view),'Use V2, V1 withdrawn.')
            flat=c._flatten_checkpoint('## Previous Checkpoint\nV1\n## Previous Checkpoint\nV1 withdrawn, V2 required.\nExact archive: old.jsonl')
            self.assertNotIn('Previous Checkpoint',flat)
            self.assertIn('V1 withdrawn, V2 required.',flat)
            self.assertIn('old.jsonl',flat)

    def test_automatic_retrieval_excludes_own_session_and_exact_covered_content(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            m=MemoryManager(workspace=root,session_path=root/'session.jsonl',session_id='case-shared',
                model_client=SummaryClient(),profile=ModelProfile('fake'),environment={
                    'MINICLAW_MEMORY_VECTOR_ENABLED':'false','MINICLAW_MEMORY_CROSS_ENCODER_ENABLED':'false'})
            for session, summary in [('case-shared','shipping same-task old phase'),('other','shipping independent session')]:
                m.episodic.checkpoint(session,[ChatMessage(role='user',content=summary)],summary=summary,status='completed')
            m.active_messages=[ChatMessage(role='user',content='shipping latest phase')]
            items=m.retrieve('shipping',automatic=True)
            self.assertFalse(any(i.metadata.get('session_id')=='case-shared' for i in items))
            self.assertTrue(any(i.metadata.get('session_id')=='other' for i in items))
            content='shipping configuration uses strict atomic output replacement and preserves existing user files on all validation errors'
            m.semantic.remember('project',content)
            m.active_messages.append(ChatMessage(role='assistant',content=content))
            self.assertFalse(any(i.content==content for i in m.retrieve('shipping configuration',automatic=True)))
            self.assertTrue(any(i.content==content for i in m.retrieve('shipping configuration',automatic=False)))

    async def test_shared_eval_identity_tracks_the_file_not_phase(self):
        class Captured(Exception):pass
        with tempfile.TemporaryDirectory() as d:
            for mode in ('shared','isolated'):
                captured=[]
                def build(**kwargs):
                    captured.append((kwargs['session_id'],kwargs['session_path']))
                    raise Captured()
                with patch('MiniClaw.evaluation.runner.CodingAssistant',side_effect=build), \
                     patch('MiniClaw.evaluation.runner.load_llm_settings',return_value=SimpleNamespace(provider='fake')), \
                     patch('MiniClaw.evaluation.runner.create_model_client'), \
                     patch('MiniClaw.evaluation.runner.model_profile_from_settings',return_value=ModelProfile('fake')):
                    for index in range(2):
                        with self.assertRaises(Captured):
                            await _run_phase(SimpleNamespace(id='case',session_mode=mode),f'turn{index}','task',{},index,Path(d),{},None,None)
                self.assertEqual(captured[0]==captured[1],mode=='shared')


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_delivers_with_no_optional_tools_and_new_request_reopens(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'app.py').write_text('value=3')
            model=ScriptedModelClient([AssistantReply(content='Delivered.'),AssistantReply(content='New request answered.')])
            assistant=recovery_helpers.RecoveryTests().assistant(model,d)
            assistant.task_progress.checkpoint('work',[],'stale: recheck everything',[
                {'criterion_id':'c','description':'value three','version':1}])
            result=ToolResult('',details={'stdout':'log\n'+json.dumps({'task_checks':[
                {'criterion_id':'c','version':1,'passed':True,'evidence':{'actual':3},'artifacts':['app.py']}]})+'\nSMOKE_OK',
                'stderr':'','exit_code':0,'workspace_changes':{'complete':True,'changed_paths':[]}})
            await assistant._task_result_transform(ToolInvocation('verify','bash',{'task_verification':True}),result)
            self.assertTrue(assistant.task_progress.ready_to_deliver())
            self.assertEqual(assistant._request_tools(),[])
            [e async for e in assistant.loop.run('deliver')]
            self.assertEqual(model.requests[0].tools,[])
            (root/'app.py').write_text('value=4')
            self.assertTrue(assistant._request_tools())
            await assistant._task_result_transform(ToolInvocation('verify2','bash',{'task_verification':True}),result)
            self.assertEqual(assistant._request_tools(),[])
            [e async for e in assistant.run('New requirement: explain the file.')]
            self.assertTrue(model.requests[1].tools)

    async def test_unavailable_hallucinated_tool_is_not_executed(self):
        with tempfile.TemporaryDirectory() as d:
            model=ScriptedModelClient([AssistantReply(tool_calls=[ToolInvocation('bad','write',{'path':'bad.txt','content':'bad'})]),AssistantReply(content='done')])
            assistant=recovery_helpers.RecoveryTests().assistant(model,d)
            assistant.loop.request_tools_provider=lambda:[]
            events=[e async for e in assistant.loop.run('deliver')]
            self.assertFalse((Path(d)/'bad.txt').exists())
            self.assertTrue(any(e.details.get('not_started') for e in events if e.type=='tool_finished'))
