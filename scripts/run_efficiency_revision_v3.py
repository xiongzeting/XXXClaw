"""Freeze current fixes against exactly the v2 exposed six-case comparison."""
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

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'.aster/evals/efficiency-revision-v2'
OUT=ROOT/'.aster/evals/efficiency-revision-v3'
SNAPSHOT=OUT/'snapshot'
SUITE='boundary-efficiency-v3.json'


def prepare():
    assert not SNAPSHOT.exists(), 'A started revision cannot be refrozen'
    previous=json.loads((OLD/'freeze.json').read_text(encoding='utf-8'))
    assert hashes(OLD/'snapshot/evals')==previous['eval_hashes']
    original=json.loads((OLD/'snapshot/evals/boundary-efficiency-v2.json').read_text(encoding='utf-8'))
    suite=json.loads(json.dumps(original)); suite['name']='boundary-efficiency-v3-exposed-regression'
    assert suite['cases']==original['cases']
    shutil.copytree(ROOT/'src',SNAPSHOT/'src',ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.egg-info'))
    shutil.copytree(OLD/'snapshot/evals',SNAPSHOT/'evals')
    dump(SNAPSHOT/'evals'/SUITE,suite)
    shutil.copy2(OLD/'oracle-calibration.json',OUT/'oracle-calibration.json')
    dump(OUT/'freeze.json',{'created_at':datetime.now(timezone.utc).isoformat(),
        'source_hashes':hashes(SNAPSHOT/'src'),'eval_hashes':hashes(SNAPSHOT/'evals'),
        'selected':SELECTED,'jobs':2,'model':'gpt-5.6-luna',
        'baseline_report_sha256':hashlib.sha256((OLD/'run/report.json').read_bytes()).hexdigest(),
        'v2_cases_prompts_fixtures_oracles_budgets_preserved':True,
        'note':'Exposed regression. No new blind-test claim. Network failures retain original costs and recovery records.'})
    print('Frozen six original cases, unchanged oracles and budgets; two Luna lanes.',flush=True)


async def run():
    os.environ.update(ENV)
    freeze=json.loads((OUT/'freeze.json').read_text(encoding='utf-8'))
    assert hashes(SNAPSHOT/'src')==freeze['source_hashes']
    assert hashes(SNAPSHOT/'evals')==freeze['eval_hashes']
    assert not (OUT/'execution.json').exists(), 'Do not replay a started batch; recover only affected requests/cases.'
    sys.path.insert(0,str(SNAPSHOT/'src'))
    from MiniClaw.evaluation.models import load_eval_suite
    from MiniClaw.evaluation.runner import run_eval_suite
    from MiniClaw.llm.env_file import merged_environment, read_env_file
    env=merged_environment(read_env_file(ROOT/'.env'));env.update(ENV)
    dump(OUT/'execution.json',{'started_at':datetime.now(timezone.utc).isoformat(),'jobs':2,
        'model':'gpt-5.6-luna','cases':list(SELECTED),'pid':os.getpid()})
    try:
        report=await run_eval_suite(load_eval_suite(SNAPSHOT/'evals'/SUITE),output_directory=OUT/'run',
            environment=env,provider='primary',model_id='gpt-5.6-luna',selected_cases=set(SELECTED),jobs=2,repeat=1)
        assert hashes(SNAPSHOT/'src')==freeze['source_hashes']
        assert hashes(SNAPSHOT/'evals')==freeze['eval_hashes']
        dump(OUT/'completion.json',{'finished_at':datetime.now(timezone.utc).isoformat(),
            'frozen_verified':True,'passed':report['summary']['passed']})
        print('Completed:',report['summary']['passed'],'/',report['summary']['cases'],flush=True)
    except Exception as exc:
        dump(OUT/'interruption.json',{'at':datetime.now(timezone.utc).isoformat(),'error':f'{type(exc).__name__}: {exc}'})
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','run']);args=parser.parse_args()
    if args.mode=='prepare':prepare()
    else:asyncio.run(run())
