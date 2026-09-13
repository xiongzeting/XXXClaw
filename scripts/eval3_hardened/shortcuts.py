"""Negative controls: empty/constant, sample-only, missing recovery, and false verification."""
import copy
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(Path(__file__).parent))
from validate import run_probe,write
from build import TARGET

def main():
    specs=json.loads((TARGET/'judge-spec.json').read_text('utf-8'))
    out=ROOT/'.aster/evals/eval3-hardened-r1/offline';jobs=[];results=[]
    selected=set(sys.argv[1:]) if len(sys.argv)>1 else set(specs)
    for cid,spec in specs.items():
        if cid not in selected:continue
        key=spec['key']
        if key in ('transaction','policy','memory'):
            answer=spec['answer']
            wrong=[{},None,{'status':'done'},{'answer':answer}]
            results.append({'id':cid,'negative_controls':4,'rejected':sum(x!=answer for x in wrong),'basis':'exact public answer semantics; no model run'})
            continue
        for index in range(4):
            work=out/'negative'/cid/str(index)
            shutil.copytree(out/'gold'/cid,work,dirs_exist_ok=True)
            if key in ('parser','invoice'):
                module='parser' if key=='parser' else 'calculator';p=work/'tests'/f'test_{module}.py'
                options=[(TARGET/'fixtures'/cid/'tests'/p.name).read_text('utf-8'),'import pytest\n@pytest.mark.skip\ndef test_skip():pass\n','def test_always_fail():assert False\n','def test_always_pass():assert True\n']
                p.write_text(options[index],'utf-8')
            elif key in ('metrics','authorization'):
                name='metrics.json' if key=='metrics' else 'final.json'
                original=json.loads((work/name).read_text('utf-8'))
                wrong=[{},None,{'status':'done'},copy.deepcopy(original)]
                if key=='metrics':wrong[3]['totals']={}
                else:wrong[3]['total']+=1
                write(work/name,wrong[index])
            elif key in ('worker','gateway','batch','upgrade','extract'):
                text=(work/'engine.py').read_text('utf-8')
                extra=['\ndef execute(root,request,fault=None):return None\n',
                       '\n_original=execute\ndef execute(root,request,fault=None):return _original(root,request,None)\n',
                       '\n_original=execute\ndef execute(root,request,fault=None):\n try:return _original(root,request,fault)\n except Exception:return None\n',
                       '\n_original=execute\ndef execute(root,request,fault=None):\n import tempfile\n with tempfile.TemporaryDirectory() as d:return _original(d,request,fault)\n'][index]
                (work/'engine.py').write_text(text+extra,'utf-8')
            elif key in ('release','cli'):
                filename='release.py' if key=='release' else 'cli.py'
                (work/filename).write_text(['raise SystemExit(0)\n','raise SystemExit(1)\n','print("all tests passed")\n','raise SystemExit(2)\n'][index],'utf-8')
            else:
                text=(work/'solution.py').read_text('utf-8');sample=spec['sample'];expected=spec['expected'].get('value')
                extra=[f'\ndef solve(data):return {expected!r}\n',f'\n_original=solve\ndef solve(data):\n if data!={sample!r}:raise ValueError("sample only")\n return _original(data)\n',
                       '\n_original=solve\ndef solve(data):\n result=_original(data)\n if isinstance(data,dict):data["mutated"]=True\n return result\n',
                       '\n_original=solve\ndef solve(data):\n try:return _original(data)\n except ValueError:return None\n'][index]
                if key=='protocol' and index==3:
                    extra='\n_original=solve\ndef solve(data):\n import copy\n data=copy.deepcopy(data)\n if "items" in data:\n  for row in data["items"]:row["version"]=data.get("version",1)\n return _original(data)\n'
                (work/'solution.py').write_text(text+extra,'utf-8')
            jobs.append((cid,index,work))
    def one(job):
        cid,index,work=job;result=run_probe(cid,work)
        observations=result.get('observations',[])
        rejected=any(r.get('matches') is False for r in observations)
        # Probe failures are not proof of successful negative control detection.
        write(out/'negative-evidence'/cid/f'{index}.json',result)
        return cid,index,rejected
    with ThreadPoolExecutor(max_workers=4) as pool:
        raw=list(pool.map(one,jobs))
    for cid in specs:
        rs=[r for r in raw if r[0]==cid]
        if rs:results.append({'id':cid,'negative_controls':len(rs),'rejected':sum(r[2] for r in rs),'unrejected':[r[1] for r in rs if not r[2]]})
    if selected!=set(specs):
        previous=json.loads((out/'shortcut-validation.json').read_text('utf-8'))['cases']
        results += [r for r in previous if r['id'] not in selected]
    ready=len(results)==20 and all(r['negative_controls']==4 and r['rejected']==4 for r in results)
    write(out/'shortcut-validation.json',{'ready':ready,'cases':results})
    print(json.dumps({'ready':ready,'cases':len(results),'failures':[r for r in results if r['rejected']!=4]}),flush=True)
    if not ready:sys.exit(1)

if __name__=='__main__':main()
