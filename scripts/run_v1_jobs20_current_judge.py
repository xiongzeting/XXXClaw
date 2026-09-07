"""Run immutable v1 agents in 20 async lanes, then score with frozen current evaluator."""
import argparse
import asyncio
import ast
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'.aster/evals/boundary-v1-jobs20-current-judge'
OLD = ROOT/'.aster/evals/boundary-campaign-v1'
CURRENT = ROOT/'.aster/evals/boundary-full-v10-jobs20/snapshot/evals'


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def hashes(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def verify():
    freeze = read(OUT/'freeze.json')
    for folder in ('agent/src', 'agent/evals', 'judge/src', 'judge/evals'):
        assert hashes(OUT/folder) == freeze['hashes'][folder], folder
    return freeze


def prepare():
    assert not OUT.exists(), 'Never overwrite or replay an existing batch'
    original = read(OLD/'freeze.json')
    assert hashes(OLD/'snapshot/src') == original['source_hashes']
    assert hashes(OLD/'snapshot/evals') == original['eval_hashes']
    image = subprocess.check_output(['docker','image','inspect','miniclaw-runtime:py313-bench',
                                    '--format','{{.Id}}'],text=True).strip()
    assert image == original['docker_image_id'], 'Historical Docker image differs'
    tree = ast.parse((ROOT/'scripts/run_boundary_campaign_v1.py').read_text(encoding='utf-8'))
    env = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
               and any(isinstance(t,ast.Name) and t.id=='ENV' for t in n.targets))
    for source, dest in [(OLD/'snapshot/src','agent/src'),(OLD/'snapshot/evals','agent/evals'),
                         (ROOT/'src','judge/src'),(CURRENT,'judge/evals')]:
        shutil.copytree(source, OUT/dest, ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    before = read(OUT/'agent/evals/boundary-campaign-v1.json')
    after = read(OUT/'judge/evals/boundary-submissions-v10.json')
    assert len(before['cases']) == len(after['cases']) == 20
    for case in before['cases']:
        peer = next(c for c in after['cases'] if c['id']==case['id'])
        for field in ('phases','fixture','environment','timeout_seconds','budgets','session_mode','goal'):
            assert case.get(field) == peer.get(field), (case['id'],field)
    dump(OUT/'freeze.json', {'created_at':datetime.now(timezone.utc).isoformat(),
        'jobs':20,'model':'gpt-5.6-luna','v1_environment_overrides':env,'docker_image_id':image,
        'hashes':{p:hashes(OUT/p) for p in ('agent/src','agent/evals','judge/src','judge/evals')},
        'main_source_before':hashes(ROOT/'src'),
        'configuration_note':'Original v1 code, suite, case settings and ENV; connection credentials and dotenv fallback from current .env, as original launcher. Historical .env was not archived.',
        'grading':'Current frozen evaluator: outcome deferred to LLM judge; four dimensions program-scored. No grading feedback to agent.',
        'isolation':'Main source never replaced. Twenty async lanes call the original v1 attempt runner, bypassing only its outer jobs<=16 limit; model components share the same process as in the original runner.'})
    shutil.copy2(Path(__file__),OUT/'launcher.py')
    print('Verified v1 code, inputs, case configurations and Docker image; current evaluator frozen.',flush=True)


async def worker(case_id):
    freeze = verify()
    os.environ.update(freeze['v1_environment_overrides'])
    sys.path.insert(0,str(OUT/'agent/src'))
    from MiniClaw.evaluation.models import load_eval_suite
    from MiniClaw.evaluation.runner import _run_case_attempt
    from MiniClaw.coding_agent.assistant import coding
    from MiniClaw.llm.env_file import merged_environment, read_env_file
    assert OUT/'agent/src' in Path(coding.__file__).parents
    env=merged_environment(read_env_file(ROOT/'.env'))
    env.update(freeze['v1_environment_overrides'])
    suite=load_eval_suite(OUT/'agent/evals/boundary-campaign-v1.json')
    case=next(c for c in suite.cases if c.id==case_id)
    destination=OUT/'run/cases'/case_id
    assert not destination.exists(), 'Never replay a started case'
    # Scoring-only fields removed from old outer runner; agent inputs/timeout/goal unchanged.
    attempt=await _run_case_attempt(suite,replace(case, checks=(), budgets={}),destination/'attempt-001',
        env,provider='primary',model_id='gpt-5.6-luna',attempt_index=1)
    attempt.update(passed=None,grading_status='pending',checks=[],dimensions={})
    workspace=Path(attempt['workspace'])
    files={p.relative_to(workspace).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
           for p in workspace.rglob('*') if not p.is_symlink() and p.is_file()
           and '.aster' not in p.relative_to(workspace).parts}
    dump(destination/'result.json',{'id':case.id,'category':case.category,'source':case.source,
                                  **attempt,'attempts':[attempt],'artifact_sha256':files})
    dump(OUT/'run/judge-packets'/(case.id+'.json'),{
        'case_id':case.id,'judge_status':'pending','workspace':attempt['workspace'],
        'requirements':[{'phase':p.id,'prompt':p.prompt} for p in case.phases],
        'answers':[{'phase':k,'final_answer':p.get('final_text',''),'errors':p.get('errors',[]),
                    'trace_path':p.get('trace_path')} for k,p in attempt['phases'].items()],
        'artifact_sha256':files,'execution_error':attempt['error'],'metrics':attempt['metrics']})


def grade():
    verify()
    sys.path.insert(0,str(OUT/'judge/src'))
    from MiniClaw.evaluation.models import load_eval_suite
    from MiniClaw.evaluation.runner import _aggregate_metrics, _combine_metrics, read_trace_records
    from MiniClaw.evaluation.submissions import score_program_dimensions, PROGRAM_DIMENSIONS
    suite=load_eval_suite(OUT/'judge/evals/boundary-submissions-v10.json')
    results=[]
    for case in suite.cases:
        path=OUT/'run/cases'/case.id/'result.json'
        if not path.exists(): continue
        raw=read(path); traces={}
        for phase_id,p in raw['phases'].items():
            records=read_trace_records(Path(p['trace_path']))
            ids=set(p.get('run_ids') or [])
            traces[phase_id]=[r for r in records if not ids or r.get('run_id') in ids]
        metrics=_aggregate_metrics(traces)
        metrics['wall_duration_seconds']=raw['duration_seconds']
        scored={**raw,'metrics':metrics}
        scored.update(score_program_dimensions(case,scored,path.parent/'attempt-001',traces=traces))
        results.append(scored)
    summary={'cases':len(results),'jobs':20,'outcome_status':'pending','passed':None,
        'dimensions':{d:{'passed':sum(r['dimensions'][d]['score']==1 for r in results),
                         'scored':sum(r['dimensions'][d]['score'] is not None for r in results)}
                      for d in PROGRAM_DIMENSIONS},'metrics':_combine_metrics([r['metrics'] for r in results])}
    dump(OUT/'run/program-report.json',{'summary':summary,'cases':results})
    print('Program scoring complete:',summary['dimensions'],flush=True)


async def run():
    verify()
    assert not (OUT/'execution.json').exists(), 'Do not relaunch the whole batch'
    cases=read(OUT/'agent/evals/boundary-campaign-v1.json')['cases']
    dump(OUT/'execution.json',{'pid':os.getpid(),'started_at':datetime.now(timezone.utc).isoformat(),
                             'jobs':20,'model':'gpt-5.6-luna','cases':[c['id'] for c in cases]})
    async def lane(case):
        log=OUT/'logs';log.mkdir(exist_ok=True)
        dump(log/(case['id']+'.process.json'),{'pid':os.getpid(),'lane':case['id']})
        try:
            await worker(case['id'])
            return {'id':case['id'],'exit_code':0}
        except Exception as exc:
            dump(log/(case['id']+'.error.json'),{'error':f'{type(exc).__name__}: {exc}'})
            return {'id':case['id'],'exit_code':1}
    states=await asyncio.gather(*(lane(c) for c in cases))
    dump(OUT/'worker-completion.json',states)
    grader=await asyncio.create_subprocess_exec(sys.executable,'-X','utf8',str(Path(__file__)),'grade')
    grade_exit=await grader.wait()
    freeze=verify()
    dump(OUT/'completion.json',{'finished_at':datetime.now(timezone.utc).isoformat(),
         'collected':sum(s['exit_code']==0 for s in states),'grader_exit_code':grade_exit,
         'frozen_verified':True,'main_source_unchanged':hashes(ROOT/'src')==freeze['main_source_before'],
         'restore_required':False,'outcome_status':'pending'})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','run','worker','grade'])
    parser.add_argument('--case');args=parser.parse_args()
    if args.mode=='prepare':prepare()
    elif args.mode=='grade':grade()
    elif args.mode=='worker':asyncio.run(worker(args.case))
    else:asyncio.run(run())
