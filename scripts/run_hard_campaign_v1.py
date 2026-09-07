"""Freeze inputs and source, then run one complete parallel Luna campaign.

No intermediate results are read, no result-based retries or oracle changes.
Use --prepare once after authoring, then --execute in a fresh process.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'evals'
CAMPAIGN = ROOT / '.aster/evals/hard-campaign-v1'
SNAPSHOT = CAMPAIGN / 'snapshot'
PARTS = ['hard-memory-v1.json', 'hard-delivery-v1.json', 'hard-safety-v1.json']
DIMS = {'outcome', 'process', 'efficiency', 'safety', 'reliability'}
ENV = {
    'MINICLAW_LLM_MAX_RETRIES': '4',
    'MINICLAW_LLM_RETRY_BASE_SECONDS': '3',
    'MINICLAW_LLM_RETRY_MAX_SECONDS': '30',
    'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1',
    'OPENBLAS_NUM_THREADS': '1', 'TOKENIZERS_PARALLELISM': 'false',
    'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1',
}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def prepare():
    from MiniClaw.evaluation.models import load_eval_suite
    assert not SNAPSHOT.exists(), 'Snapshot already exists; never refreeze after results'
    cases = []
    for filename in PARTS:
        part = read(E / filename)
        for c in part['cases']:
            c['environment'] = {**part.get('environment', {}), **c.get('environment', {})}
            cases.append(c)
    assert len(cases) == 30
    assert len({c['id'] for c in cases}) == len(cases)
    families = {}
    prompts = set()
    split_counts = {}
    for c in cases:
        split = c['source']['split']
        assert split in {'development', 'retained', 'test'}
        family = c['source']['family']
        assert family not in families or families[family] == split, family
        families[family] = split
        digest = hashlib.sha256('\n'.join(p['prompt'] for p in c['phases']).encode()).hexdigest()
        assert digest not in prompts, c['id']
        prompts.add(digest)
        assert DIMS <= {x.get('dimension', 'outcome') for x in c['checks'] if x.get('required', True)}, c['id']
        for x in c['checks']:
            if x.get('dimension') in {'efficiency', 'reliability'} and x['type'] == 'metric':
                assert x.get('min') != 0, c['id']
        assert c['environment'].get('MINICLAW_SANDBOX') == 'docker:miniclaw-runtime:py313-bench', c['id']
    for split in ('development', 'retained', 'test'):
        selected = [c for c in cases if c['source']['split'] == split]
        counts = Counter(c['category'] for c in selected)
        assert counts == dict.fromkeys(('recall', 'compression', 'tools', 'completion', 'safety'), 2), counts
        split_counts[split] = dict(counts)
        filename = f'{split}-challenge-v3.json'
        dump(E / filename, {'version': 1, 'name': f'{split}-challenge-v3',
             'coverage': {'dimensions': sorted(DIMS)},
             'exposure_policy': 'Assigned before first execution; failures stay in original split. Inspected failures exported for development cannot later count as unseen.',
             'cases': selected})
        load_eval_suite(E / filename)
    # Interleave capabilities and splits in the shared pool.
    cases.sort(key=lambda c: (c['source']['split'], c['category'], c['id']))
    groups = {cat: [c for c in cases if c['category'] == cat]
              for cat in ('recall', 'tools', 'compression', 'safety', 'completion')}
    ordered = [group[i] for i in range(6) for group in groups.values()]
    dump(E / 'hard-campaign-v1.json', {'version': 1, 'name': 'hard-campaign-v1',
         'coverage': {'dimensions': sorted(DIMS)}, 'cases': ordered})
    suite = load_eval_suite(E / 'hard-campaign-v1.json')
    source_before = hashes(ROOT / 'src')
    shutil.copytree(ROOT / 'src', SNAPSHOT / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    assert source_before == hashes(ROOT / 'src') == hashes(SNAPSHOT / 'src'), 'Source changed while copying'
    snapshot_evals = SNAPSHOT / 'evals'
    snapshot_evals.mkdir()
    for filename in PARTS + ['hard-campaign-v1.json'] + [f'{s}-challenge-v3.json' for s in split_counts]:
        shutil.copy2(E / filename, snapshot_evals / filename)
    for case in suite.cases:
        if not case.fixture:
            continue
        origin = (case.fixture_root / case.fixture).resolve()
        origin.relative_to(E.resolve())
        target = snapshot_evals / case.fixture
        if not target.exists():
            shutil.copytree(origin, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        assert hashes(origin) == hashes(target), case.id
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'model': 'gpt-5.6-luna',
                'cases': 30, 'splits': split_counts, 'jobs': 2, 'source_hashes': source_before,
                'eval_hashes': hashes(snapshot_evals), 'environment_overrides': ENV,
                'docker_image_id': 'sha256:45cec89a174723cc81718ce1e5724b32d918528efc56f3ec9e338e4ba554da93',
                'policy': 'Frozen pre-run source and inputs; one first-pass attempt each; no mid-run result inspection; first-pass failures retained.'}
    dump(CAMPAIGN / 'freeze.json', manifest)
    dump(E / 'hard-campaign-freeze-v1.json', manifest)
    print('Frozen 30 cases, 3 splits, 5 abilities, all required dimensions; isolated source snapshot')


async def execute():
    os.environ.update(ENV)
    manifest = read(CAMPAIGN / 'freeze.json')
    assert hashes(SNAPSHOT / 'src') == manifest['source_hashes']
    assert hashes(SNAPSHOT / 'evals') == manifest['eval_hashes']
    sys.path.insert(0, str(SNAPSHOT / 'src'))
    from MiniClaw.evaluation.models import load_eval_suite
    from MiniClaw.evaluation.runner import run_eval_suite
    import MiniClaw.evaluation.runner as runner
    from MiniClaw.llm.env_file import merged_environment, read_env_file
    assert Path(runner.__file__).resolve().is_relative_to(SNAPSHOT.resolve())
    env = merged_environment(read_env_file(ROOT / '.env'))
    env.update(ENV)
    dump(CAMPAIGN / 'execution.json', {'started_at': datetime.now(timezone.utc).isoformat(),
         'runner': str(runner.__file__), 'python': sys.version, 'jobs': 2,
         'source_frozen': True, 'model': 'gpt-5.6-luna'})
    report = await run_eval_suite(load_eval_suite(SNAPSHOT / 'evals/hard-campaign-v1.json'),
                                 output_directory=CAMPAIGN / 'run', environment=env,
                                 provider='primary', model_id='gpt-5.6-luna', jobs=2, repeat=1)
    assert hashes(SNAPSHOT / 'src') == manifest['source_hashes']
    assert hashes(SNAPSHOT / 'evals') == manifest['eval_hashes']
    current = hashes(ROOT / 'src')
    drift = sorted(k for k in current.keys() | manifest['source_hashes'].keys()
                   if current.get(k) != manifest['source_hashes'].get(k))
    dump(CAMPAIGN / 'completion.json', {'finished_at': datetime.now(timezone.utc).isoformat(),
         'frozen_source_verified': True, 'frozen_inputs_verified': True,
         'live_workspace_source_drift': drift, 'note': 'Live edits do not affect isolated run.'})
    s = report['summary']
    print(f'Whole batch complete: {s["passed"]}/{s["cases"]}; first-pass attempts only', flush=True)
    return int(s['failed'] > 0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.prepare:
        prepare()
    else:
        raise SystemExit(asyncio.run(execute()))
