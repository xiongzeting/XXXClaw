"""Canonical runner for direct, Feishu input and real-router dialogue cases.

Runs actual gpt-5.6-luna calls and returns nonzero for unresolved regressions.
No live Feishu service is contacted. Jobs is a total concurrent-case limit.
"""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import sys

from MiniClaw.evaluation.models import load_eval_suite


async def main(args):
    if not 1<=args.jobs<=16:raise ValueError('jobs must be 1..16')
    if args.repeat is not None and not 1<=args.repeat<=20:raise ValueError('repeat must be 1..20')
    suite=load_eval_suite(args.suite)
    cases=list(suite.cases)
    if args.cases:
        selected=set(args.cases)
        if selected-{c.id for c in cases}:raise ValueError('Unknown case selection')
        cases=[c for c in cases if c.id in selected]
    out=Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):raise FileExistsError(out)
    out.mkdir(parents=True,exist_ok=True)
    core=[c.id for c in cases if c.source.get('input_adapter')!='feishu-router']
    router=[c.id for c in cases if c.source.get('input_adapter')=='feishu-router']
    split=bool(core and router)
    router_jobs=max(1,args.jobs//3) if split else args.jobs
    core_jobs=max(1,args.jobs-router_jobs) if split else args.jobs
    async def run_group(name,ids,jobs):
        script='run_dialogue_router_eval.py' if name=='router' else 'run_dialogue_adapter_eval.py'
        cmd=[sys.executable,str(Path(__file__).with_name(script)),str(Path(args.suite).resolve()),
             '--out',str(out/name),'--env-file',str(Path(args.env_file).resolve()),'--jobs',str(jobs)]
        if args.repeat is not None:cmd+=['--repeat',str(args.repeat)]
        for cid in ids:cmd+=['--case',cid]
        with (out/f'{name}.log').open('wb') as log:
            process=await asyncio.create_subprocess_exec(*cmd,stdout=log,stderr=asyncio.subprocess.STDOUT)
            exit_code=await process.wait()
        summary_path=out/name/'summary.json'
        if not summary_path.exists():raise RuntimeError(f'{name} runner failed; inspect {out / (name+".log")}')
        return {'name':name,'exit_code':exit_code,'summary':json.loads(summary_path.read_text(encoding='utf-8'))}
    tasks=[]
    if args.jobs==1:
        if core:tasks.append(await run_group('core',core,1))
        if router:tasks.append(await run_group('router',router,1))
    else:
        calls=[]
        if core:calls.append(run_group('core',core,core_jobs))
        if router:calls.append(run_group('router',router,router_jobs))
        tasks=await asyncio.gather(*calls)
    summary={'suite':suite.name,'cases':sum(t['summary']['cases'] for t in tasks),
        'passed':sum(t['summary']['passed'] for t in tasks),'failed':sum(t['summary']['failed'] for t in tasks),
        'attempts':sum(t['summary']['attempts'] for t in tasks),'jobs':args.jobs,
        'metrics':{k:sum(t['summary']['metrics'].get(k,0) for t in tasks)
                   for k in ('total_tokens','input_tokens','output_tokens','cost_usd','model_requests')},
        'groups':tasks,'known_limitations':['No live Feishu API','No aggregate percentile claim','Costs are configured-rate estimates']}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"Portfolio: {summary['passed']}/{summary['cases']} passed, {summary['attempts']} attempts -> {out}",flush=True)
    return 1 if summary['failed'] or any(t['exit_code'] for t in tasks) else 0


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('suite');p.add_argument('--out',required=True);p.add_argument('--env-file',default='.env')
    p.add_argument('--jobs',type=int,default=6);p.add_argument('--repeat',type=int)
    p.add_argument('--case',action='append',dest='cases')
    raise SystemExit(asyncio.run(main(p.parse_args())))
