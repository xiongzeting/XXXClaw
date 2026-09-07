"""Real Docker execution and binding repair on Windows, with zero model requests."""
import asyncio
import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.runtime.config import RuntimeSettings
from MiniClaw.llm.types import ModelProfile, ToolInvocation
from smoke_verification_v5 import NoModel


async def main():
    checks = []
    with tempfile.TemporaryDirectory(prefix='miniclaw-v8-docker-') as d:
        root = Path(d)
        (root/'input.json').write_text('{"items":[2,3]}', encoding='utf-8')
        (root/'probe.py').write_text("import json,pathlib\np=pathlib.Path('count')\np.write_text(str(int(p.read_text())+1) if p.exists() else '1')\nv=json.load(open('input.json'))\nprint(json.dumps({'total':sum(v['items'])}))\n", encoding='utf-8')
        assistant = CodingAssistant(NoModel(), ModelProfile('no-model'), root,
            runtime_settings=RuntimeSettings(backend='docker', docker_image='miniclaw-runtime:py313-bench'),
            environment={'MINICLAW_MEMORY_ENABLED':'false', 'MINICLAW_MEMORY_CONSOLIDATION_ENABLED':'false',
                         'MINICLAW_GOAL_JUDGE_ENABLED':'false', 'MINICLAW_APPROVAL_POLICY':'allow'})
        p = assistant.task_progress
        p.begin_user_turn('Verify formal input sum')
        p.checkpoint('sum', [], 'verify', [{'criterion_id':'sum', 'version':1, 'description':'sum input',
                                           'kind':'data', 'check_ids':['total']}])
        spec = [{'criterion_id':'sum', 'artifacts':['input.json','probe.py'], 'comparisons':[
            {'check_id':'total', 'expected':5, 'expected_source':'input.json items 2+3'}]}]
        for path in ['input.json', '/workspace/input.json', str(root/'input.json')]:
            current = copy.deepcopy(spec); current[0]['artifacts'][0] = path
            result = await assistant.tool_executor.execute(ToolInvocation('valid-'+str(len(checks)), 'bash', {
                'command':'python probe.py', 'verification':current, 'timeout':20}))
            assert not result.is_error and not p.pending(), result.content
            checks.append({'test':'bare_output_omitted_pointer', 'artifact_path':path, 'passed':True})
        wrong = copy.deepcopy(spec); wrong[0]['comparisons'][0]['pointer'] = '/missing'
        result = await assistant.tool_executor.execute(ToolInvocation('original', 'bash', {
            'command':'python probe.py', 'verification':wrong, 'timeout':20}))
        assert result.is_error and 'available keys' in result.content
        identifier = result.details['evidence_id']
        counter = (root/'count').read_text()
        args = {'evidence_id':identifier, 'bindings':[{'criterion_id':'sum', 'check_id':'total', 'pointer':'/total'}]}
        repaired = await assistant.tool_executor.execute(ToolInvocation('repair', 'verification_rebind', args))
        assert not repaired.is_error and not p.pending(), repaired.content
        assert (root/'count').read_text() == counter
        assert p.state['verified']['sum']['call_id'] == 'original'
        checks.append({'test':'rebind_without_docker_execution', 'passed':True})
        (root/'input.json').write_text('{"items":[9]}', encoding='utf-8')
        rejected = await assistant.tool_executor.execute(ToolInvocation('stale', 'verification_rebind', args))
        assert rejected.is_error and 'changed' in rejected.content and (root/'count').read_text() == counter
        checks.append({'test':'changed_artifacts_cannot_reuse_evidence', 'passed':True})
        outside = copy.deepcopy(spec); outside[0]['artifacts'] = ['../escape.json']
        rejected = await assistant.tool_executor.execute(ToolInvocation('outside', 'bash', {
            'command':'python probe.py', 'verification':outside, 'timeout':20}))
        assert rejected.is_error and rejected.details['not_started'] and (root/'count').read_text() == counter
        checks.append({'test':'outside_path_rejected_before_execution', 'passed':True})
        result = await assistant.tool_executor.execute(ToolInvocation('real-mismatch', 'bash', {
            'command':'python probe.py', 'verification':spec, 'timeout':20}))
        assert p.state['failed_observations']['sum'][0]['actual'] == 9 and p.final_blocker()
        checks.append({'test':'real_wrong_value_rejected', 'passed':True})
        result = await assistant.tool_executor.execute(ToolInvocation('nonzero', 'bash', {
            'command':'python probe.py; exit 7', 'verification':spec, 'timeout':20}))
        assert result.is_error and 'evidence_id' not in result.details
        checks.append({'test':'nonzero_cannot_be_rebound_to_pass', 'passed':True})
    output = ROOT/'.aster/evals/efficiency-revision-v8-smoke.json'
    output.write_text(json.dumps({'model_requests':0, 'checks':checks}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'passed':len(checks), 'model_requests':0, 'report':str(output)}, ensure_ascii=False))


if __name__ == '__main__': asyncio.run(main())
