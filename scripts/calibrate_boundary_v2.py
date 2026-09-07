"""Calibrate revised public-contract oracles without model calls or original-score edits."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from MiniClaw.evaluation.oracle import evaluate_oracle
from boundary_delivery_specs import SPECS

SCRIPT=ROOT/'evals/oracles/boundary_v2.py'
spec=importlib.util.spec_from_file_location('boundary_v2',SCRIPT)
oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
OUT=ROOT/'.aster/evals/efficiency-revision-v2'


def options(key):
    return {'script':str(SCRIPT),'sha256':hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
            'entry':key,'requirements':oracle.REQUIREMENTS[key],'timeout_seconds':90}


def files(root, values):
    for name,body in values.items():
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(body,encoding='utf-8')


def calibrate(s):
    s={**s,'reference_files':dict(s['reference_files'])}
    if s['key']=='queue_rebuild_and_reconcile':
        body=s['reference_files']['queue_tool.py']
        body=body.replace("  if x.get('seq')!=i or x.get('op')", "  if x.get('op')")
        body=body.replace("  seen[x['seq']]=sig", "  if x.get('seq') != len(seen)+1: raise ValueError('gap')\n  seen[x['seq']]=sig")
        s['reference_files']['queue_tool.py']=body
    if s['key']=='atomic_batch_reservation':
        body=s['reference_files']['inventory.py']
        # Independent known-positive control for synchronous injected I/O failures.
        # It is a calibration artifact, never copied to an Agent workspace.
        wrapper="""
_reserve_impl = reserve
def reserve(path,request_id,lines):
 p=Path(path); sf=p/'stock.json'; rf=p/'reservations.json'
 before=(sf.read_bytes(),rf.read_bytes())
 try: return _reserve_impl(path,request_id,lines)
 except OSError:
  sf.write_bytes(before[0]);rf.write_bytes(before[1]);raise
"""
        s['reference_files']['inventory.py']=body.replace("if __name__=='__main__':",wrapper+"\nif __name__=='__main__':")
    runs=[]
    variants=[('reference',s['reference_files'])]
    if s['key'] in {'atomic_batch_reservation','queue_rebuild_and_reconcile'}:
        alternate=dict(s['reference_files'])
        if s['key']=='queue_rebuild_and_reconcile':
            alternate['queue_tool.py']=alternate['queue_tool.py'].replace("st={'active':active}","st=active")
        else:
            alternate['inventory.py']=alternate['inventory.py'].replace("t.replace(f)","__import__('os').replace(t,f)")
        variants.append(('alternate_reference',alternate))
    variants += [(f'mutant-{i}',m) for i,m in enumerate(s['mutants'])]
    for label,variant in variants:
        with tempfile.TemporaryDirectory(prefix='oracle-v2-') as d:
            p=Path(d);files(p,s['files']);files(p,s['reference_files']);files(p,variant)
            result=evaluate_oracle(options(s['key']),p)
            runs.append({'variant':label,**result})
    old=ROOT/'.aster/evals/boundary-campaign-v1/run/cases'/('boundary_v1_'+s['key'])/'attempt-001/workspace'
    runs.append({'variant':'previous_delivery',**evaluate_oracle(options(s['key']),old)})
    positives=[r for r in runs if r['variant'] in ('reference','alternate_reference','previous_delivery') and r['passed']]
    negatives=[r for r in runs if r['variant'].startswith('mutant-')]
    calibrated=len(positives)>=2 and len(negatives)>=2 and all(r['classification']=='capability_failure' for r in negatives)
    return {'case':s['key'],'calibrated':calibrated,'positive_implementations':len(positives),'runs':runs}


if __name__=='__main__':
    OUT.mkdir(parents=True,exist_ok=True)
    selected=[s for s in SPECS if not sys.argv[1:] or s['key'] in sys.argv[1:]]
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows=list(pool.map(calibrate,selected))
    (OUT/'oracle-calibration-draft.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    if len(rows)==6 and all(r['calibrated'] for r in rows):
        (OUT/'oracle-calibration.json').write_text(json.dumps({'sha256':hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),'cases':rows},ensure_ascii=False,indent=2),encoding='utf-8')
    for row in rows:
        print(row['case'],[(r['variant'],r['classification'],[(c['requirement_id'],c['evidence']) for c in r.get('checks',[]) if not c['passed']],r.get('error')) for r in row['runs']])
