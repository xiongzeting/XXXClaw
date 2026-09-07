"""Freeze and run six unchanged, exposed regressions with six Luna lanes."""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

from run_efficiency_revision_v2 import ENV, SELECTED, dump, hashes

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT/'.aster/evals/efficiency-revision-v5'
OUT = ROOT/'.aster/evals/efficiency-revision-v9'
SNAPSHOT = OUT/'snapshot'
SUITE = 'boundary-efficiency-v9.json'
JOBS = 6


def prepare():
    assert not SNAPSHOT.exists(), 'Never refreeze a started revision'
    previous = json.loads((BASELINE/'freeze.json').read_text(encoding='utf-8'))
    assert hashes(BASELINE/'snapshot/evals') == previous['eval_hashes']
    original = json.loads((BASELINE/'snapshot/evals/boundary-efficiency-v5.json').read_text(encoding='utf-8'))
    suite = json.loads(json.dumps(original))
    suite['name'] = 'boundary-efficiency-v9-exposed-regression'
    assert suite['cases'] == original['cases']
    assert set(SELECTED).issubset({c['id'] for c in suite['cases']})
    validation_path = ROOT/'.aster/evals/efficiency-revision-v9-validation.json'
    validation = json.loads(validation_path.read_text(encoding='utf-8'))
    assert validation['success']
    for path, digest in validation['source_sha256'].items():
        assert hashlib.sha256((ROOT/'src/MiniClaw'/path).read_bytes()).hexdigest() == digest, path
    shutil.copytree(ROOT/'src', SNAPSHOT/'src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.egg-info'))
    shutil.copytree(BASELINE/'snapshot/evals', SNAPSHOT/'evals')
    dump(SNAPSHOT/'evals'/SUITE, suite)
    shutil.copy2(BASELINE/'oracle-calibration.json', OUT/'oracle-calibration.json')
    shutil.copy2(validation_path, OUT/'preflight-validation.json')

    for runner in (Path(__file__), ROOT/'scripts/run_efficiency_revision_v2.py'):
        shutil.copy2(runner, SNAPSHOT/runner.name)
    dump(OUT/'freeze.json', {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source_hashes': hashes(SNAPSHOT/'src'), 'eval_hashes': hashes(SNAPSHOT/'evals'),
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'selected': SELECTED, 'jobs': JOBS, 'model': 'gpt-5.6-luna',
        'baseline_report_sha256': hashlib.sha256((BASELINE/'run/report.json').read_bytes()).hexdigest(),
        'v5_cases_prompts_fixtures_oracles_budgets_preserved': True,
        'implementation': 'v9 registered verification contracts, observation emitter, diagnostic separation, categorized feedback and history net-cost accounting; no intermediate pause',
        'metric_notes': 'Feedback v3 separates plan, execution, protocol and comparison failures; pending history is separate. Preserve raw errors and unresolved_error for prior-version comparisons. Classification is not token savings.',
        'note': 'Exposed regression, not unseen tests. Retain raw network faults and costs. Six concurrent lanes; no controlled latency/cache claim.',
    })
    print('Frozen v9 implementation; six unchanged cases and budgets, six Luna lanes.', flush=True)


async def run():
    os.environ.update(ENV)
    freeze = json.loads((OUT/'freeze.json').read_text(encoding='utf-8'))
    assert hashes(SNAPSHOT/'src') == freeze['source_hashes']
    assert hashes(SNAPSHOT/'evals') == freeze['eval_hashes']
    assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == freeze['runner_sha256']
    assert not (OUT/'execution.json').exists(), 'Recover interrupted requests; never replay a started batch'
    sys.path.insert(0, str(SNAPSHOT/'src'))
    from MiniClaw.evaluation.models import load_eval_suite
    from MiniClaw.evaluation.runner import run_eval_suite
    from MiniClaw.llm.env_file import merged_environment, read_env_file
    from MiniClaw.coding_agent.assistant import verification_rebind
    assert SNAPSHOT/'src' in Path(verification_rebind.__file__).parents
    env = merged_environment(read_env_file(ROOT/'.env')); env.update(ENV)
    dump(OUT/'execution.json', {
        'started_at': datetime.now(timezone.utc).isoformat(), 'jobs': JOBS,
        'model': 'gpt-5.6-luna', 'cases': list(SELECTED), 'pid': os.getpid(),
        'implementation': freeze['implementation'],
    })
    try:
        report = await run_eval_suite(load_eval_suite(SNAPSHOT/'evals'/SUITE),
            output_directory=OUT/'run', environment=env, provider='primary', model_id='gpt-5.6-luna',
            selected_cases=set(SELECTED), jobs=JOBS, repeat=1)
        assert hashes(SNAPSHOT/'src') == freeze['source_hashes']
        assert hashes(SNAPSHOT/'evals') == freeze['eval_hashes']
        dump(OUT/'completion.json', {'finished_at': datetime.now(timezone.utc).isoformat(),
            'frozen_verified': True, 'passed': report['summary']['passed']})
        print('Completed:', report['summary']['passed'], '/', report['summary']['cases'], flush=True)
    except Exception as exc:
        dump(OUT/'interruption.json', {'at': datetime.now(timezone.utc).isoformat(),
            'error': f'{type(exc).__name__}: {exc}'})
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['prepare', 'run'])
    args = parser.parse_args()
    if args.mode == 'prepare': prepare()
    else: asyncio.run(run())
