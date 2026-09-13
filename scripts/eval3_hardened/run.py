"""One frozen five-lane DeepSeek submission run with phase snapshots and filesystem audit."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
ROOT=Path(__file__).resolve().parents[2]
TARGET=ROOT/'evals/eval3-hardened-r1'
STAMP=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
JOBS=5
MODEL='deepseek-v4-flash'
PROVIDER='deepseek'
OUT=ROOT/'.aster/evals'/f'eval3-hardened-r1-deepseek{JOBS}-{STAMP}'
CASE_IDS = {
    'acceptance_property_matrix',
    'debug_multi_service_timeout',
    'acceptance_mutation_survivors',
    'requirements_backward_compat_gate',
    'recovery_partial_commit',
}
sys.path.insert(0,str(Path(__file__).parent))
from watch import DirectoryWatch,self_test

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n','utf-8')

def freeze():
    validation=json.loads((ROOT/'.aster/evals/eval3-hardened-r1/offline/validation.json').read_text('utf-8'))
    shortcuts=json.loads((ROOT/'.aster/evals/eval3-hardened-r1/offline/shortcut-validation.json').read_text('utf-8'))
    if not validation['ready'] or not shortcuts['ready']:raise RuntimeError('Offline validation is not ready')
    OUT.mkdir(parents=True)
    shutil.copytree(ROOT/'src',OUT/'snapshot/src',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(TARGET,OUT/'snapshot/evals',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(Path(__file__).parent,OUT/'snapshot/harness',ignore=shutil.ignore_patterns('__pycache__'))
    manifest={p.relative_to(OUT/'snapshot').as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (OUT/'snapshot').rglob('*') if p.is_file()}
    write(OUT/'freeze.json',{'revision':'eval3-hardened-r1','created_utc':STAMP,'files':manifest,'jobs':JOBS,'repeat':1,'model':MODEL,'selected_cases':sorted(CASE_IDS),'exposure':'exposed strengthened regression','offline_validation':validation})
    write(ROOT/'.aster/evals/eval3-hardened-r1/active-run.json',{'directory':str(OUT),'state':'prepared'})

async def main():
    freeze()
    sys.path.insert(0,str(OUT/'snapshot/src'))
    from MiniClaw.evaluation.models import load_eval_suite
    from MiniClaw.evaluation import runner
    from MiniClaw.llm.env_file import merged_environment,read_env_file
    from MiniClaw.llm.config import load_llm_settings
    env=merged_environment(read_env_file(ROOT/'.env') if (ROOT/'.env').is_file() else {})
    env.update({'MINICLAW_EVAL_DEFER_JUDGE':'true','MINICLAW_DEEPSEEK_MODEL':MODEL})
    settings=load_llm_settings(provider=PROVIDER,model_id=MODEL,environment=env)
    write(OUT/'runtime-config.json',{'model':settings.model_id,'base_url':settings.base_url,'jobs':JOBS,'repeat':1,'timeout_seconds':settings.timeout_seconds,'max_retries':settings.max_retries,'retry_base_seconds':settings.retry_base_seconds,'retry_max_seconds':settings.retry_max_seconds,'context_window':settings.context_window,'max_output_tokens':settings.max_output_tokens,'prices':{'input':settings.input_cost_per_million,'cached':settings.cached_input_cost_per_million,'output':settings.output_cost_per_million},'price_status':'configured' if any([settings.input_cost_per_million,settings.output_cost_per_million]) else 'unavailable','fallback_count':len(settings.fallbacks)})
    if settings.fallbacks:raise RuntimeError('This run requires DeepSeek only; remove fallback configuration')
    test=self_test(OUT);write(OUT/'watcher-self-test.json',test)
    cases_root=OUT/'run/cases';cases_root.mkdir(parents=True)
    # Submission runner requires its root empty. Watch the prepared parent instead.
    cases_root.rmdir();(OUT/'run').rmdir()
    active={};watch=DirectoryWatch(OUT,OUT/'filesystem-events.jsonl',active)
    # Watcher root normally is cases/. Adapt incoming paths by pointing at run/cases after creation.
    watch.kernel.CloseHandle(watch.handle)
    original=runner._run_phase
    initialized=False
    watchers=[]
    async def phase(case,phase_id,prompt,control,index,workspace,environment,provider,model_id):
        nonlocal initialized
        if not initialized:
            watcher=DirectoryWatch(OUT/'run/cases',OUT/'filesystem-events.jsonl',active);watcher.start();watchers.append(watcher);initialized=True
        active[case.id]=phase_id
        start=time.time()
        print(json.dumps({'event':'phase_started','case':case.id,'phase':phase_id},ensure_ascii=False),flush=True)
        result=None
        try:
            result=await original(case,phase_id,prompt,control,index,workspace,environment,provider,model_id)
            return result
        finally:
            await asyncio.sleep(.2)
            active.pop(case.id,None)
            dst=OUT/'phase-evidence'/case.id/phase_id
            dst.mkdir(parents=True,exist_ok=True)
            files={}
            for p in workspace.rglob('*'):
                rel=p.relative_to(workspace)
                if '.aster' in rel.parts or p.is_symlink() or not p.is_file():continue
                if any(parent.is_symlink() for parent in p.parents if parent!=workspace and workspace in parent.parents):continue
                raw=p.read_bytes();files[rel.as_posix()]=hashlib.sha256(raw).hexdigest()
                target=dst/'workspace'/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            write(dst/'phase.json',{'case_id':case.id,'phase':phase_id,'started_at':start,'ended_at':time.time(),'file_sha256':files,'phase_result':result})
            print(json.dumps({'event':'phase_finished','case':case.id,'phase':phase_id,'errors':result.get('errors',[]) if result else ['interrupted']},ensure_ascii=False),flush=True)
    runner._run_phase=phase
    start=time.time();write(OUT/'run-status.json',{'state':'running','started_at':start})
    try:
        report=await runner.run_eval_suite(load_eval_suite(OUT/'snapshot/evals/suite.json'),output_directory=OUT/'run',environment=env,selected_cases=CASE_IDS,provider=PROVIDER,model_id=MODEL,jobs=JOBS,repeat=1)
        write(OUT/'report.json',report);write(OUT/'run-status.json',{'state':'collected_pending_judge','started_at':start,'ended_at':time.time(),'cases':len(report['cases'])})
    except BaseException as exc:
        write(OUT/'run-status.json',{'state':'interrupted','started_at':start,'ended_at':time.time(),'error_type':type(exc).__name__,'error':str(exc)})
        raise
    finally:
        for watcher in watchers:watcher.stop()
        write(OUT/'watcher-status.json',{'errors':[e for w in watchers for e in w.errors],'observes':'Windows directory changes; writes/deletes, not file reads'})
    print(json.dumps({'output':str(OUT),'cases':len(CASE_IDS),'jobs':JOBS,'outcome':'pending assistant judge'}),flush=True)

if __name__=='__main__':asyncio.run(main())
