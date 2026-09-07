"""Freeze an exposed regression subset; preserve v1 prompts, inputs and budgets."""
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

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'.aster/evals/boundary-campaign-v1'
OUT=ROOT/'.aster/evals/efficiency-revision-v2'
SNAPSHOT=OUT/'snapshot'
SELECTED={
 'boundary_v1_atomic_batch_reservation':'最高 token 消耗、验收循环和提交故障',
 'hard_v1_tiered_invoice':'旧压缩题最高成本，重复读取和真实金额语义失败',
 'hard_v1_redaction_priority':'高成本、重复读取、触顶暂停',
 'hard_v1_shipping_caps':'高成本、多次压缩',
 'boundary_v1_config_migration_cli_contract':'判定器误报和多文件集成成本',
 'boundary_v1_compression_dependency_lock_retraction':'压缩中撤回/版本冲突，防止省 token 损伤字段语义',
}
ENV={'MINICLAW_LLM_MAX_RETRIES':'4','MINICLAW_LLM_RETRY_BASE_SECONDS':'3',
     'MINICLAW_LLM_RETRY_MAX_SECONDS':'30','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1',
     'OPENBLAS_NUM_THREADS':'1','TOKENIZERS_PARALLELISM':'false','PYTHONIOENCODING':'utf-8',
     'PYTHONDONTWRITEBYTECODE':'1'}


def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def hashes(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def prepare():
    assert not SNAPSHOT.exists(),'A started revision cannot be refrozen'
    from calibrate_boundary_v2 import options,SCRIPT
    calibration=json.loads((OUT/'oracle-calibration.json').read_text(encoding='utf-8'))
    assert calibration['sha256']==hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    assert len(calibration['cases'])==6 and all(r['calibrated'] for r in calibration['cases'])
    suite=json.loads((OLD/'snapshot/evals/boundary-campaign-v1.json').read_text(encoding='utf-8'))
    original=json.loads(json.dumps(suite))
    revised={row['case'] for row in calibration['cases']}
    for case in suite['cases']:
        entry=case['id'].removeprefix('boundary_v1_')
        if entry in revised:
            case['checks'][0]={'type':'oracle','dimension':'outcome',**options(entry),'script':'oracles/boundary_v2.py'}
        case['source']={**case['source'],'original_split':case['source'].get('split'),
                        'split':'development','exposure':'exposed regression; not an unseen retained/test score',
                        'oracle_revision':'boundary-v2','original_case_id':case['id']}
        before=next(c for c in original['cases'] if c['id']==case['id'])
        for field in ['phases','fixture','environment','timeout_seconds','budgets','session_mode']:
            assert case.get(field)==before.get(field),(case['id'],field)
        assert case['checks'][1:]==before['checks'][1:]
    suite['name']='boundary-efficiency-v2-exposed-regression'
    dump(ROOT/'evals/boundary-efficiency-v2.json',suite)
    # Source and test code are frozen together; model workspaces receive only original fixtures.
    shutil.copytree(ROOT/'src',SNAPSHOT/'src',ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.egg-info'))
    (SNAPSHOT/'evals/oracles').mkdir(parents=True)
    shutil.copy2(SCRIPT,SNAPSHOT/'evals/oracles/boundary_v2.py')
    for case in suite['cases']:
        source=OLD/'snapshot/evals'/case['fixture']
        destination=SNAPSHOT/'evals'/case['fixture']
        if not destination.exists(): shutil.copytree(source,destination,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    dump(SNAPSHOT/'evals/boundary-efficiency-v2.json',suite)
    freeze={'created_at':datetime.now(timezone.utc).isoformat(),'source_hashes':hashes(SNAPSHOT/'src'),
            'eval_hashes':hashes(SNAPSHOT/'evals'),'selected':SELECTED,'jobs':2,'model':'gpt-5.6-luna',
            'original_report_sha256':hashlib.sha256((OLD/'run/report.json').read_bytes()).hexdigest(),
            'original_prompts_inputs_budgets_preserved':True,
            'note':'All cases are exposed regression; original scores remain unchanged. Fault checks are revised measurements.'}
    dump(OUT/'freeze.json',freeze)
    print('Frozen 6 selected cases. Original prompts, fixtures, runtime settings and budgets retained.')


async def run():
    os.environ.update(ENV)
    freeze=json.loads((OUT/'freeze.json').read_text(encoding='utf-8'))
    assert hashes(SNAPSHOT/'src')==freeze['source_hashes']
    assert hashes(SNAPSHOT/'evals')==freeze['eval_hashes']
    sys.path.insert(0,str(SNAPSHOT/'src'))
    from MiniClaw.evaluation.models import load_eval_suite
    from MiniClaw.evaluation.runner import run_eval_suite
    from MiniClaw.llm.env_file import merged_environment,read_env_file
    env=merged_environment(read_env_file(ROOT/'.env'));env.update(ENV)
    dump(OUT/'execution.json',{'started_at':datetime.now(timezone.utc).isoformat(),'jobs':2,
                             'model':'gpt-5.6-luna','cases':list(SELECTED),'pid':os.getpid()})
    try:
        report=await run_eval_suite(load_eval_suite(SNAPSHOT/'evals/boundary-efficiency-v2.json'),
                                  output_directory=OUT/'run',environment=env,provider='primary',
                                  model_id='gpt-5.6-luna',selected_cases=set(SELECTED),jobs=2,repeat=1)
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
    if args.mode=='prepare': prepare()
    else: asyncio.run(run())
