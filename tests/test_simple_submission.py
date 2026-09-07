import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.runtime import RuntimeSettings
from MiniClaw.llm.types import AssistantReply, ModelProfile, ToolInvocation
from tests.test_agent_loop import ScriptedModelClient


class SimpleSubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_twenty_lane_collection_program_scores_but_defers_outcome(self):
        from MiniClaw.evaluation.models import EvalSuite, EvalCase, EvalPhase, EvalCheck
        from MiniClaw.evaluation.runner import run_eval_suite
        import asyncio
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);active=0;peak=0
            async def phase(case, phase_id, prompt, control, index, workspace, *args, **kwargs):
                nonlocal active,peak
                active+=1;peak=max(peak,active);await asyncio.sleep(.02);active-=1
                (workspace/'forbidden.txt').write_text('unauthorized')
                return {'final_text':'plain answer','run_ids':[],'errors':[], 'trace_path':str(workspace/'no-trace')}
            cases=tuple(EvalCase(id='case'+str(i),category='sample',fixture=None,
                phases=(EvalPhase('turn1','do work'),),checks=(EvalCheck('llm_rubric'),
                    EvalCheck('metric', 'process', options={'name':'tool_errors','max':0}),
                    EvalCheck('metric', 'efficiency', options={'name':'total_tokens','min':1}),
                    EvalCheck('file_absent', 'safety', options={'path':'forbidden.txt'}),
                    EvalCheck('final_regex', 'reliability', options={'pattern':r'\S'}),
                    EvalCheck('command', 'outcome', options={'command':'must never execute'}),
                )) for i in range(20))
            suite=EvalSuite('test',1,root/'suite.json',cases)
            with patch('MiniClaw.evaluation.runner._run_phase',side_effect=phase), \
                 patch('MiniClaw.evaluation.runner._evaluate_llm_rubric',new_callable=AsyncMock) as judge:
                report=await run_eval_suite(suite,output_directory=root/'out',
                    environment={'MINICLAW_EVAL_DEFER_JUDGE':'true'},jobs=20)
                judge.assert_not_called()
            self.assertEqual(peak,20)
            self.assertIsNone(report['summary']['passed'])
            self.assertEqual(len(list((root/'out/judge-packets').glob('*.json'))),20)
            self.assertEqual(report['cases'][0]['grading_status'],'pending')
            dimensions=report['cases'][0]['dimensions']
            self.assertIsNone(dimensions['outcome']['score'])
            self.assertEqual(dimensions['process']['score'],1)
            self.assertEqual(dimensions['efficiency']['score'],0)
            self.assertEqual(dimensions['safety']['score'],0)
            self.assertEqual(dimensions['reliability']['score'],1)
            self.assertEqual(len(report['cases'][0]['checks']),4)

    def assistant(self, root, replies, backend='host'):
        self.client=ScriptedModelClient(replies)
        return CodingAssistant(self.client,ModelProfile('test'),root,
            runtime_settings=RuntimeSettings(backend=backend,docker_image='miniclaw-runtime:py313-bench'),
            environment={'MINICLAW_MEMORY_ENABLED':'false','MINICLAW_MEMORY_CONSOLIDATION_ENABLED':'false',
                         'MINICLAW_APPROVAL_POLICY':'allow','MINICLAW_GOAL_JUDGE_ENABLED':'false'})

    async def test_plain_final_answer_saved_without_schema_or_extra_request(self):
        with tempfile.TemporaryDirectory() as d:
            # Old verifier state must not revive the removed protocol.
            p=Path(d)/'.aster';p.mkdir();(p/'task-progress.json').write_text('{bad old state')
            a=self.assistant(d,[AssistantReply(content='已经完成。详见 solution.py。')])
            events=[e async for e in a.run('请正常回答')]
            self.assertEqual(len(self.client.requests),1)
            defs={x['name']:x for x in a.tool_executor.definitions()}
            self.assertLessEqual(set(defs),{'write','bash','read','edit','grep','search','memory','goal','goal_complete','skill'})
            self.assertEqual(set(defs['bash']['parameters']['properties']),{'command','timeout'})
            self.assertNotIn('task_checkpoint',a.loop.system_prompt)
            packet=json.loads(next((p/'submissions').glob('*.json')).read_text())
            self.assertEqual(packet['final_answer'],'已经完成。详见 solution.py。')
            self.assertEqual(packet['judge_status'],'pending')

    async def test_command_errors_continue_then_submit_without_verification_protocol(self):
        with tempfile.TemporaryDirectory() as d:
            replies=[AssistantReply(tool_calls=[ToolInvocation(str(i),'bash',{'command':'exit 1'})]) for i in range(4)]
            replies+=[AssistantReply(tool_calls=[ToolInvocation('ok','bash',{'command':'echo CHECK_OK'})]),AssistantReply(content='done')]
            a=self.assistant(d,replies);events=[e async for e in a.run('run checks')]
            self.assertEqual(len(self.client.requests),6)
            self.assertEqual(a.loop.max_turns,32)
            self.assertFalse(any('Invalid verification' in (e.message.content if e.message else '') for e in events))

    async def test_goal_complete_only_requires_answer_and_no_judge(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.assistant(d,[]);a.create_goal('write result',['deliver answer'])
            a._provide_system_prompt()
            r=await a.tool_executor.execute(ToolInvocation('done','goal_complete',{'final_result':'My final result'}))
            self.assertFalse(r.is_error,r.content)
            self.assertEqual(r.details['judge_status'],'pending')
            self.assertEqual(a.goal_store.read().status,'complete')

    async def test_docker_plain_output_and_file_write(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.assistant(d,[],backend='docker')
            r=await a.tool_executor.execute(ToolInvocation('write','bash',{'command':'echo hello > answer.txt; cat answer.txt'}))
            self.assertFalse(r.is_error,r.content)
            self.assertEqual((Path(d)/'answer.txt').read_text().strip(),'hello')
            self.assertNotIn('verification_plan',r.details)

    async def test_new_tool_registration_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            a=self.assistant(d,[])
            class Extra:
                name='new_tool'
            with self.assertRaises(ValueError):a.tool_executor.register(Extra())
