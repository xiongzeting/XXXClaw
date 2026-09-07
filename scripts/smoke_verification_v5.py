"""Windows + Docker verification smoke; no LLM requests or benchmark tasks."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.runtime.config import RuntimeSettings
from MiniClaw.llm.types import ModelProfile, ToolInvocation

class NoModel:
    async def stream(self, request):
        raise AssertionError('This smoke must not call a model')
        yield

async def main():
    checks=[]
    with tempfile.TemporaryDirectory(prefix='miniclaw-v5-docker-') as d:
        root=Path(d)
        (root/'input.json').write_text('{"items":[2,3]}',encoding='utf-8')
        (root/'probe.py').write_text("import json\nv=json.load(open('input.json'))\nprint(json.dumps({'observations':{'total':sum(v['items'])}}))\n",encoding='utf-8')
        assistant=CodingAssistant(NoModel(),ModelProfile('no-model'),root,
            runtime_settings=RuntimeSettings(backend='docker',docker_image='miniclaw-runtime:py313-bench'),
            environment={'MINICLAW_MEMORY_ENABLED':'false','MINICLAW_MEMORY_CONSOLIDATION_ENABLED':'false',
                         'MINICLAW_GOAL_JUDGE_ENABLED':'false','MINICLAW_APPROVAL_POLICY':'allow'})
        p=assistant.task_progress
        p.begin_user_turn('Verify the sum of formal input')
        p.checkpoint('sum',[],'verify',[{'criterion_id':'sum','version':1,'description':'sum input','kind':'data','check_ids':['total']}])
        spec=[{'criterion_id':'sum','artifacts':['input.json','probe.py'], 'comparisons':[
            {'check_id':'total','pointer':'/total','expected':5,'expected_source':'input.json items 2+3'}]}]
        for path in ['input.json','/workspace/input.json',str(root/'input.json')]:
            current=copy.deepcopy(spec);current[0]['artifacts'][0]=path
            result=await assistant.tool_executor.execute(ToolInvocation('positive-'+str(len(checks)),'bash',{
                'command':'python probe.py','verification':current,'timeout':20}))
            assert not result.is_error, result.content
            assert not p.pending(), p.state
            checks.append({'test':'valid_observation_path', 'path':path,'passed':True,'runtime':result.details.get('runtime')})
        bad=copy.deepcopy(spec);bad[0]['comparisons'][0]['expected']=6
        result=await assistant.tool_executor.execute(ToolInvocation('negative','bash',{'command':'python probe.py','verification':bad,'timeout':20}))
        assert p.pending() and p.snapshot()['failed_observations']['sum'][0]['actual']==5
        checks.append({'test':'wrong_expected_rejected','passed':True})
        outside=copy.deepcopy(spec);outside[0]['artifacts']=['../escape.json']
        result=await assistant.tool_executor.execute(ToolInvocation('boundary','bash',{
            'command':"python -c 'open(\"should-not-run\",\"w\").write(\"x\")'",'verification':outside,'timeout':20}))
        assert result.is_error and not (root/'should-not-run').exists()
        checks.append({'test':'outside_artifact_rejected_before_execution','passed':True})
        result=await assistant.tool_executor.execute(ToolInvocation('nonzero','bash',{'command':'python probe.py; exit 7','verification':spec,'timeout':20}))
        assert result.is_error and p.final_blocker()
        checks.append({'test':'nonzero_command_cannot_pass','passed':True})
    output=ROOT/'.aster/evals/efficiency-revision-v5-smoke.json'
    output.write_text(json.dumps({'model_requests':0,'checks':checks},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':len(checks),'model_requests':0,'report':str(output)},ensure_ascii=False))

if __name__=='__main__':asyncio.run(main())
