"""Resume an externally interrupted frozen Eval without copying fixtures or replaying tools."""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import time
from run_efficiency_revision_v2 import ENV, dump, hashes

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.aster/evals/efficiency-revision-v4'
SNAP=OUT/'snapshot'
RECOVERY=OUT/'recovery'
sys.path.insert(0,str(SNAP/'src'))
from MiniClaw.evaluation import runner as r
from MiniClaw.evaluation.models import load_eval_suite
from MiniClaw.llm.env_file import merged_environment, read_env_file
from MiniClaw.coding_agent.assistant.coding import CodingAssistant


def records(path):
    values=[]
    if not path.exists():return values
    for line in path.read_text(encoding='utf-8').splitlines():
        try:values.append(json.loads(line))
        except ValueError:pass
    return values


def inspect(case):
    work=OUT/'run/cases'/case.id/'attempt-001/workspace'
    session=work/'.aster/eval-sessions/shared'
    events=records(session/'trace.jsonl')
    by_prompt={p.prompt:p.id for p in case.phases}
    starts={e['run_id']:e for e in events if e['type']=='run.started'}
    ends={e['run_id']:e for e in events if e['type']=='run.completed'}
    phases={}
    for runid,start in starts.items():
        pid=by_prompt.get(start['data']['request'])
        if not pid:continue
        phase=next(p for p in case.phases if p.id==pid)
        value=phases.setdefault(pid,{'id':pid,'prompt':phase.prompt,'final_text':'','run_ids':[],
            'errors':[],'goal':None,'trace_path':str(session/'trace.jsonl'),
            'session_path':str(session/'session.jsonl'),'provider':'primary','model':'gpt-5.6-luna',
            'control':dict(phase.control),'ended':False})
        value['run_ids'].append(runid)
        value['ended']=runid in ends
        if runid in ends:
            data=ends[runid]['data'];value['final_text']=data.get('final_text','');value['goal']=data.get('goal')
            if data.get('error'):value['errors'].append(data['error'])
    state=json.loads((session/'run-state.json').read_text(encoding='utf-8')) if (session/'run-state.json').exists() else {}
    calls=set();returned=set()
    for row in records(session/'session.jsonl'):
        message=row.get('message',{})
        calls.update(c['call_id'] for c in message.get('tool_calls',[]))
        if message.get('role')=='tool':returned.add(message.get('tool_call_id'))
    assert not calls-returned, f'{case.id}: unresolved tool calls require manual state audit'
    assert not state.get('active_tool_call_id'), f'{case.id}: tool may still be executing'
    unfinished=[p for p in case.phases if not phases.get(p.id,{}).get('ended')]
    current=unfinished[0] if unfinished else None
    resumed=bool(current and current.id in phases)
    if resumed:
        assert state.get('prompt')==current.prompt and state.get('status') in {'waiting_model','interrupted'}, (case.id,state.get('status'))
        successful=sum(e['type']=='model.request' and e['run_id']==state['run_id'] and
                       e['data'].get('purpose')=='agent' for e in events)
        start_turn=max(state['turn'],successful+1)
    else:start_turn=1
    span=0.0
    for runid,start in starts.items():
        if runid in ends:span+=ends[runid]['data'].get('duration_ms',0)/1000
        else:
            tail=max(e['timestamp'] for e in events if e.get('run_id')==runid)
            span+=(datetime.fromisoformat(tail.replace('Z','+00:00'))-datetime.fromisoformat(start['timestamp'].replace('Z','+00:00'))).total_seconds()
    return {'case':case.id,'workspace':str(work),'session':str(session),'phases':phases,
            'resume_phase':current.id if resumed else None,'next_phase':current.id if current else None,
            'start_turn':start_turn,'prior_run_id':state.get('run_id') if resumed else None,
            'observed_active_seconds':span,'completed_phases':sum(v['ended'] for v in phases.values()),
            'required_phases':len(case.phases),'unresolved_tool_calls':0}


PLANS={}


class ResumeAssistant(CodingAssistant):
    async def run(self,prompt):
        plan=PLANS.get(str(self.workspace))
        if plan and plan.get('prior_run_id') and self.recovered_run_state and self.recovered_run_state.run_id==plan['prior_run_id']:
            self._task_updated_this_run=bool(self.task_progress.state.get('criteria'))
            original=self.trace_recorder.record
            parent=plan['prior_run_id']
            def record(kind,data,**kwargs):
                if kind=='run.started' and data.get('network_recovery_of')==parent:
                    data={**data,'process_recovery_of':parent,'interruption_cause':'unknown_process_exit'}
                    data.pop('network_recovery_of',None)
                return original(kind,data,**kwargs)
            self.trace_recorder.record=record
            self.trace_recorder.record('eval.process_recovery',{'prior_run_id':parent,'start_turn':plan['start_turn'],
                'remaining_turns':max(0,self.loop.max_turns-plan['start_turn']+1),
                'tools_replayed':False,'pending_request':'reconstructed from durable context; exact unsaved request unavailable',
                'unreported_provider_usage_possible':True})
            # Reuse the established no-new-prompt path. Relabel process recovery
            # explicitly; do not claim the unknown process exit was a network error.
            async for event in self._run_once(prompt,network_recovery_of=parent,start_turn=plan['start_turn']):yield event
        else:
            async for event in super().run(prompt):yield event


async def run_case(suite,case,base_env):
    result_path=OUT/'run/cases'/case.id/'result.json'
    if result_path.exists():return json.loads(result_path.read_text(encoding='utf-8'))
    plan=inspect(case);PLANS[plan['workspace']]=plan
    work=Path(plan['workspace']);attempt_root=work.parent
    phases=plan['phases'];env={**base_env,**suite.environment,**case.environment}
    env.setdefault('MINICLAW_APPROVAL_POLICY','allow');env.setdefault('MINICLAW_GOAL_JUDGE_ENABLED','false')
    before=r._snapshot_workspace((case.fixture_root/case.fixture).resolve())
    started=time.perf_counter();error=''
    try:
        async with asyncio.timeout(max(0.01,case.timeout_seconds-plan['observed_active_seconds'])):
            for index,phase in enumerate(case.phases):
                if phases.get(phase.id,{}).get('ended'):continue
                prior=phases.get(phase.id,{})
                value=await r._run_phase(case,phase.id,phase.prompt,phase.control,index,work,env,'primary','gpt-5.6-luna')
                value['run_ids']=list(dict.fromkeys([*prior.get('run_ids',[]),*value['run_ids']]))
                value['errors']=[*prior.get('errors',[]),*value['errors']];value['ended']=True
                phases[phase.id]=value
                dump(RECOVERY/case.id/'phases.json',phases)
                print(case.id,phase.id,'ended',flush=True)
    except Exception as exc:error=f'{type(exc).__name__}: {exc}'
    wall=plan['observed_active_seconds']+time.perf_counter()-started
    traces={p:[e for e in records(Path(v['trace_path'])) if e.get('run_id') in v['run_ids']] for p,v in phases.items()}
    metrics=r._aggregate_metrics(traces);metrics['wall_duration_seconds']=round(wall,3)
    metrics['external_process_interruption']=True
    metrics['unreported_provider_usage_possible']=True
    changes=r._workspace_changes(before,r._snapshot_workspace(work))
    checks=[]
    for check in case.checks:
        assert check.type!='llm_rubric','Unexpected new evaluator; preserve the frozen checks'
        checks.append(await asyncio.to_thread(r.evaluate_check,check,work,attempt_root,phases,traces,metrics=metrics,workspace_changes=changes))
    checks+=r._evaluate_budgets(case.budgets,metrics,wall_seconds=wall)
    failures=[f"{c['dimension']}:{c['type']}:{c['detail'][:240]}" for c in checks if c.get('required',True) and not c['passed']]
    if error:failures.insert(0,'runtime:error:'+error)
    attempt={'attempt':1,'capabilities':list(case.capabilities),
        'passed':not error and len(phases)==len(case.phases) and all(c['passed'] for c in checks if c.get('required',True)),
        'error':error or None,'failure_reasons':failures,
        'failure_classes':sorted({c.get('classification') or ('efficiency_failure' if c['dimension']=='efficiency' else 'capability_failure')
            for c in checks if c.get('required',True) and not c['passed']} | ({'runtime_failure'} if error else set())),
        'started_at':json.loads((OUT/'execution.json').read_text(encoding='utf-8'))['started_at'],
        'duration_seconds':round(wall,3),'workspace':str(work),'workspace_changes':changes,'phases':phases,
        'checks':checks,'dimensions':r._dimension_scores(checks),'metrics':metrics,'coverage':r._coverage(traces,checks,phases),
        'recovery':{'external_process_exit':'unknown','idle_gap_excluded_from_active_wall':True,
                    'observed_pre_resume_seconds':plan['observed_active_seconds'],'all_recorded_token_costs_retained':True}}
    result=r._aggregate_case_attempts(case,[attempt]);dump(result_path,result)
    print(case.id,'SCORED',result['passed'],flush=True)
    return result


async def main(mode):
    os.environ.update(ENV)
    freeze=json.loads((OUT/'freeze.json').read_text(encoding='utf-8'))
    assert hashes(SNAP/'src')==freeze['source_hashes'] and hashes(SNAP/'evals')==freeze['eval_hashes']
    suite=load_eval_suite(SNAP/'evals/boundary-efficiency-v4.json')
    cases=[c for c in suite.cases if c.id in freeze['selected']]
    plans=[inspect(c) for c in cases if not (OUT/'run/cases'/c.id/'result.json').exists()]
    if mode=='inspect':
        dump(RECOVERY/'plan.json',plans)
        for p in plans:print(json.dumps({k:v for k,v in p.items() if k not in {'phases','workspace','session'}},ensure_ascii=False))
        return
    RECOVERY.mkdir(parents=True,exist_ok=True)
    lock=RECOVERY/'running.lock'
    with lock.open('x',encoding='utf-8') as handle:handle.write(str(os.getpid()))
    for p in plans:
        backup=RECOVERY/p['case']/'original-session'
        if not backup.exists():shutil.copytree(p['session'],backup)
    dump(RECOVERY/'execution.json',{'pid':os.getpid(),'started_at':datetime.now(timezone.utc).isoformat(),'jobs':6,
        'strategy':'resume incomplete phase using remaining turns, then only unstarted phases; same workspace and frozen implementation'})
    r.CodingAssistant=ResumeAssistant
    env=merged_environment(read_env_file(ROOT/'.env'));env.update(ENV)
    started=time.perf_counter()
    try:
        results=await asyncio.gather(*(run_case(suite,c,env) for c in cases))
        summary=r._summarize(suite,results,elapsed_seconds=time.perf_counter()-started,enforce_coverage=False)
        summary.update(jobs=6,baseline=None,regression_alerts=[],external_process_recovery=True,
                       elapsed_seconds_scope='recovery batch only; per-case active duration also includes pre-interruption observations')
        report={'summary':summary,'cases':results}
        dump(OUT/'run/report.json',report);dump(OUT/'run/summary.json',summary)
        (OUT/'run/report.md').write_text(r._render_markdown(report),encoding='utf-8')
        assert hashes(SNAP/'src')==freeze['source_hashes'] and hashes(SNAP/'evals')==freeze['eval_hashes']
        dump(OUT/'completion.json',{'finished_at':datetime.now(timezone.utc).isoformat(),'frozen_verified':True,
            'passed':summary['passed'],'recovered_after_external_process_exit':True})
        print('ALL SIX COMPLETED',flush=True)
    finally:
        lock.unlink(missing_ok=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['inspect','run']);args=parser.parse_args()
    asyncio.run(main(args.mode))
