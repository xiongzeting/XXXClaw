"""Exercise registered verification and helper isolation without a model."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.runtime.config import RuntimeSettings
from MiniClaw.llm.types import ModelProfile, ToolInvocation
from smoke_verification_v5 import NoModel


async def main():
    checks = []
    for backend in ['host', 'docker']:
        with tempfile.TemporaryDirectory(prefix='miniclaw-v9-') as directory:
            root = Path(directory)
            (root/'data.json').write_text('[2,3]')
            (root/'probe.py').write_text("import json\nfrom miniclaw_verification import emit\nemit({'sum':sum(json.load(open('data.json')))})\n")
            a = CodingAssistant(NoModel(), ModelProfile('no-model'), root,
                runtime_settings=RuntimeSettings(backend=backend, docker_image='miniclaw-runtime:py313-bench'),
                environment={'MINICLAW_MEMORY_ENABLED':'false','MINICLAW_MEMORY_CONSOLIDATION_ENABLED':'false',
                             'MINICLAW_GOAL_JUDGE_ENABLED':'false','MINICLAW_APPROVAL_POLICY':'allow'})
            p = a.task_progress; p.begin_user_turn('Sum formal input')
            p.checkpoint('sum', [], 'verify', [{'criterion_id':'sum','description':'sum input','kind':'data',
                'artifacts':['data.json','probe.py'], 'comparisons':[
                    {'check_id':'sum','expected':5,'expected_source':'2 + 3'}]}])
            python = 'python' if backend == 'docker' else '"' + sys.executable + '"'
            async def run(label, **arguments):
                return await a.tool_executor.execute(ToolInvocation(label, 'bash', arguments))
            r = await run('registered', command=python+' probe.py', verification=[{'criterion_id':'sum'}])
            assert not r.is_error and not p.pending(), r.content
            checks.append(backend+':registered_helper')
            r = await run('diagnostic', command='git status', purpose='diagnostic')
            assert r.is_error and not p.pending(), r.content
            checks.append(backend+':diagnostic_failure_preserves_unchanged_evidence')
            r = await run('invalid_mix', command=python+' probe.py', purpose='diagnostic', verification=[{'criterion_id':'sum'}])
            assert r.is_error and r.details.get('not_started'), r.content
            checks.append(backend+':diagnostic_verification_mix_rejected')
            (root/'data.json').write_text('[9]')
            assert p.pending()
            r = await run('mismatch', command=python+' probe.py', verification=[{'criterion_id':'sum'}])
            assert r.details['task_verification']['status'] == 'comparison_failed' and p.final_blocker(), r.content
            checks.append(backend+':wrong_value_stays_failed')
            if backend == 'docker':
                r = await run('readonly', command='touch /opt/miniclaw-verification/should_not_exist', purpose='diagnostic')
                assert r.is_error, r.content
                assert not (root/'miniclaw_verification.py').exists()
                checks.append('docker:helper_readonly_outside_workspace')
    output = ROOT/'.aster/evals/efficiency-revision-v9-smoke.json'
    output.write_text(json.dumps({'checks':checks,'passed':len(checks),'model_requests':0}, indent=2))
    print(output.read_text())


if __name__ == '__main__': asyncio.run(main())
