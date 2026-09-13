"""Read-only frozen outcome probes for a completed native Codex batch."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(json.loads((ROOT/'.aster/evals/codex-eval3/active-run.json').read_text('utf-8'))['directory'])

def one(case):
    target=OUT/'outcome-evidence'/(case['id']+'.json')
    if target.exists():return
    target.parent.mkdir(exist_ok=True)
    args=['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','128','--memory','512m','--cpus','1','--tmpfs','/tmp:rw,nosuid,size=128m',
          '--mount',f'type=bind,source={case["workspace"]},target=/workspace,readonly',
          '--mount',f'type=bind,source={OUT/"snapshot/harness"},target=/oracle,readonly',
          '--mount',f'type=bind,source={OUT/"snapshot/evals"},target=/spec,readonly',
          '-w','/workspace','-e','PYTHONDONTWRITEBYTECODE=1','miniclaw-runtime:py313-bench','python','/oracle/probe.py',case['id'],'/spec/judge-spec.json']
    try:
        proc=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=240)
        data=json.loads(proc.stdout) if proc.returncode==0 else {'case_id':case['id'],'probe_error':proc.stderr[-3000:]}
    except Exception as e:data={'case_id':case['id'],'probe_error':type(e).__name__}
    target.write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({'case':case['id'],'observations':len(data.get('observations',[])),'mismatches':sum(x.get('matches') is False for x in data.get('observations',[])),'probe_error':data.get('probe_error')}),flush=True)

if __name__=='__main__':
    status=json.loads((OUT/'run-status.json').read_text('utf-8'))
    assert status['state']=='collected_pending_judge','Do not probe a running candidate'
    cases=json.loads((OUT/'report.json').read_text('utf-8'))['cases']
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(one,cases))
