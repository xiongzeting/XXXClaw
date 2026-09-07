"""Resume incomplete frozen cases with a shared concurrency limit."""
import argparse,asyncio,json,os
from dataclasses import replace
from pathlib import Path
from MiniClaw.evaluation.models import load_eval_suite
from MiniClaw.evaluation.runner import run_eval_suite
from MiniClaw.llm.env_file import read_env_file,merged_environment

ROOT=Path(__file__).resolve().parents[1];R=ROOT/'.aster/evals/three-split-v2';E=ROOT/'evals'

async def main(args):
    groups=[];history=[]
    for split,name in [('retained','retained-release-v2.json'),('test','fresh-test-v1.json')]:
        suite=load_eval_suite(E/name)
        pending=[]
        for c in suite.cases:
            p=R/split/'cases'/c.id/'result.json'
            previous=json.loads(p.read_text(encoding='utf-8')) if p.is_file() else None
            history.append({'id':c.id,'split':split,'prior_complete':bool(previous),
                'prior_passed':previous['passed'] if previous else None,
                'prior_result':str(p.relative_to(ROOT)) if previous else None,
                'action':'keep_success' if previous and previous['passed'] else 'run_again' if previous else 'resume_pending'})
            if previous and previous['passed']:continue
            pending.append(replace(c,environment={**suite.environment,**c.environment}))
        groups.append(pending)
    selected=[]
    for i in range(max(map(len,groups))):
        for g in groups:
            if i<len(g):selected.append(g[i])
    target=R/args.output
    assert not target.exists(),target
    target.mkdir(parents=True)
    (R/(args.output+'-plan.json')).write_text(json.dumps(history,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    template=load_eval_suite(E/'fresh-test-v1.json')
    suite=replace(template,name='frozen-splits-resumption',cases=tuple(selected),environment={},required_capabilities=())
    env=merged_environment(read_env_file(ROOT/'.env'))
    report=await run_eval_suite(suite,output_directory=target,environment=env,provider='primary',model_id='gpt-5.6-luna',jobs=args.jobs,repeat=1)
    s=report['summary'];print(f'Resumed {s["passed"]}/{s["cases"]}, {s["attempts"]} attempts',flush=True)
    return int(s['failed']>0)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--jobs',type=int,default=2);p.add_argument('--output',default='recovery-1')
    raise SystemExit(asyncio.run(main(p.parse_args())))
