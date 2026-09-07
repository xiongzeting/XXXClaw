"""Freeze the cost fix and rerun two preselected hard cases, once each."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / '.aster/evals/memory-cost-v1'
OLD = ROOT / '.aster/evals/hard-campaign-v1/snapshot'
SNAPSHOT = RUN / 'snapshot'
IDS = {'hard_v1_version_resolution', 'hard_v1_multi_hop_assets'}


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def prepare():
    assert not SNAPSHOT.exists(), 'Never overwrite an executed candidate'
    shutil.copytree(ROOT / 'src', SNAPSHOT / 'src', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(OLD / 'evals', SNAPSHOT / 'evals')
    suite = json.loads((OLD / 'evals/hard-campaign-v1.json').read_text(encoding='utf-8'))
    suite['cases'] = [c for c in suite['cases'] if c['id'] in IDS]
    assert len(suite['cases']) == len(IDS)
    suite['name'] = 'memory-cost-v1-fixed-cases'
    dump(SNAPSHOT / 'evals/memory-cost-v1.json', suite)
    current, baseline = hashes(SNAPSHOT / 'src'), hashes(OLD / 'src')
    dump(RUN / 'freeze.json', {'created_at': datetime.now(timezone.utc).isoformat(),
        'cases': sorted(IDS), 'source_hashes': current, 'eval_hashes': hashes(SNAPSHOT / 'evals'),
        'changed_since_baseline': sorted(k for k in current.keys() | baseline.keys() if current.get(k) != baseline.get(k)),
        'policy': 'Fixed already-exposed cases, one attempt; no threshold/fixture changes. Not a clean holdout score.'})
    print('Frozen', len(IDS), 'cases for one candidate run')


async def execute():
    assert not (RUN / 'execution.json').exists(), 'No silent retry'
    manifest = json.loads((RUN / 'freeze.json').read_text(encoding='utf-8'))
    assert hashes(SNAPSHOT / 'src') == manifest['source_hashes']
    assert hashes(SNAPSHOT / 'evals') == manifest['eval_hashes']
    sys.path.insert(0, str(SNAPSHOT / 'src'))
    from MiniClaw.evaluation.models import load_eval_suite
    from MiniClaw.evaluation.runner import run_eval_suite
    from MiniClaw.llm.env_file import read_env_file, merged_environment
    environment = merged_environment(read_env_file(ROOT / '.env'))
    environment.update({'MINICLAW_LLM_MAX_RETRIES': '4', 'MINICLAW_LLM_RETRY_BASE_SECONDS': '3',
                        'MINICLAW_LLM_RETRY_MAX_SECONDS': '30', 'OMP_NUM_THREADS': '1',
                        'MKL_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'TOKENIZERS_PARALLELISM': 'false',
                        'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'})
    dump(RUN / 'execution.json', {'started_at': datetime.now(timezone.utc).isoformat(),
         'python': sys.version, 'model': 'gpt-5.6-luna', 'jobs': 2})
    report = await run_eval_suite(load_eval_suite(SNAPSHOT / 'evals/memory-cost-v1.json'),
        output_directory=RUN / 'run', environment=environment, provider='primary',
        model_id='gpt-5.6-luna', jobs=2, repeat=1)
    assert hashes(SNAPSHOT / 'src') == manifest['source_hashes']
    assert hashes(SNAPSHOT / 'evals') == manifest['eval_hashes']
    dump(RUN / 'completion.json', {'finished_at': datetime.now(timezone.utc).isoformat(),
                                  'snapshots_intact': True})
    print('Completed:', report['summary']['passed'], '/', report['summary']['cases'])
    return int(report['summary']['failed'] > 0)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--prepare', action='store_true')
    p.add_argument('--revision', type=int, default=1)
    p.add_argument('--case', action='append', dest='cases')
    args = p.parse_args()
    RUN = ROOT / f'.aster/evals/memory-cost-v{args.revision}'
    SNAPSHOT = RUN / 'snapshot'
    if args.cases:
        assert set(args.cases) <= IDS
        IDS = set(args.cases)
    if args.prepare:
        prepare()
    else:
        raise SystemExit(asyncio.run(execute()))
