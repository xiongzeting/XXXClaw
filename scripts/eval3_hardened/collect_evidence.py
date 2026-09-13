"""Run frozen read-only behavior probes after model execution, without assigning outcome grades."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[2]

def main():
    out=Path(json.loads((ROOT/'.aster/evals/eval3-hardened-r1/active-run.json').read_text('utf-8'))['directory'])
    status=json.loads((out/'run-status.json').read_text('utf-8'))
    if status['state']!='collected_pending_judge':raise RuntimeError('Wait for all model execution to finish')
    report=json.loads((out/'report.json').read_text('utf-8'))
    dest=out/'outcome-evidence';dest.mkdir(exist_ok=True)
    def one(case):
        target=dest/(case['id']+'.json')
        if target.exists():return {'id':case['id'],'state':'existing'}
        args=['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','128','--memory','512m','--cpus','1','--tmpfs','/tmp:rw,nosuid,size=128m',
              '--mount',f'type=bind,source={case["workspace"]},target=/workspace,readonly',
              '--mount',f'type=bind,source={out/"snapshot/harness"},target=/oracle,readonly',
              '--mount',f'type=bind,source={out/"snapshot/evals"},target=/spec,readonly',
              '-w','/workspace','-e','PYTHONDONTWRITEBYTECODE=1','miniclaw-runtime:py313-bench','python','/oracle/probe.py',case['id'],'/spec/judge-spec.json']
        try:
            p=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=240)
            data=json.loads(p.stdout) if p.returncode==0 else {'case_id':case['id'],'probe_error':p.stderr[-3000:]}
        except Exception as e:data={'case_id':case['id'],'probe_error':type(e).__name__+': '+str(e)}
        target.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n','utf-8')
        rows=data.get('observations',[])
        result={'id':case['id'],'observations':len(rows),'mismatches':sum(r.get('matches') is False for r in rows),'probe_errors':sum('error' in r for r in rows)+int('probe_error' in data)}
        print(json.dumps(result),flush=True);return result
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(one,report['cases']))
    (dest/'index.json').write_text(json.dumps({'status':'evidence-only; outcome awaits assistant','cases':results},indent=2),'utf-8')

if __name__=='__main__':main()
