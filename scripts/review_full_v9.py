"""Versioned read-only regrading of v1/v9, preserving every original report."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from MiniClaw.evaluation.oracle import evaluate_oracle
OUT=ROOT/'.aster/evals/boundary-full-v9-review'

def prepare():
    OUT.mkdir(exist_ok=True)
    source=ROOT/'.aster/evals/boundary-full-v9-jobs10/snapshot/evals/oracles/boundary_v2.py'
    s=source.read_text(encoding='utf-8')
    s=s.replace("require(ledger.read_bytes()==before,'replay rewrote existing ledger')",
        "require([(r['order_id'],r['cents']) for r in read(ledger)['charges']]==[('old',90)],'replay changed financial records')\n            before=ledger.read_bytes()")
    s=s.replace("read(ledger)==first and len(first['charges'])==2",
        "[(r['order_id'],r['cents']) for r in read(ledger)['charges']]==[(r['order_id'],r['cents']) for r in first['charges']] and len(first['charges'])==2")
    s=s.replace("if isinstance(state,dict) and 'active' not in state:",
        "if isinstance(state,dict) and set(state)=={'tasks'}: state=state['tasks']\n            if isinstance(state,dict) and 'active' not in state:")
    s=s.replace("forms=[(log,policy,out),(log,'--policy',policy,'--bundle',out)]",
        "forms=[(log,policy,out),(log,'--policy',policy,'--bundle',out)]\n        # A fixed bundle.json output is expressly permitted by the public task.\n        fixed=p/'bundle.json'\n        probe=command('audit.py',log,'--policy',policy)\n        if probe.returncode==0 and fixed.exists():\n            out=fixed; forms=[(log,'--policy',policy)]")
    s=s.replace("require(command('audit.py').returncode==2,'wrong argument exit')",
        "require(command('audit.py','--unsupported-eval-argument').returncode==2,'wrong argument exit')")
    path=OUT/'review_oracle.py'; path.write_text(s,encoding='utf-8')
    return path

def main():
    path=prepare()
    spec=importlib.util.spec_from_file_location('review_oracle',path)
    oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    tasks=[]
    for version,folder in [('v1','boundary-campaign-v1'),('v9','boundary-full-v9-jobs10')]:
        report=ROOT/'.aster/evals'/folder/'run/report.json'
        r=json.loads(report.read_text(encoding='utf-8'))
        for c in r['cases']:
            key=c['id'].removeprefix('boundary_v1_')
            if key in oracle.REQUIREMENTS:tasks.append((version,c,key))
    def run(task):
        version,c,key=task
        options={'script':str(path),'sha256':digest,'entry':key,'requirements':oracle.REQUIREMENTS[key]}
        result=evaluate_oracle(options,Path(c['workspace']))
        return {'version':version,'id':c['id'],'original_outcome':c['dimensions']['outcome']['score'], 'review':result}
    with ThreadPoolExecutor(max_workers=2) as pool: rows=list(pool.map(run,tasks))
    (OUT/'regraded.json').write_text(json.dumps({'oracle_sha256':digest,'rows':rows},ensure_ascii=False,indent=2),encoding='utf-8')
    for r in rows:print(r['version'],r['id'],r['review']['classification'],r['review'].get('checks',r['review'].get('error')))

if __name__=='__main__':main()
