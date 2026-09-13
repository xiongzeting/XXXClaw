"""Offline references, mutation sanity, and discriminating probe validation."""
import copy
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(Path(__file__).parent))
import reference as ref
import durable_reference as durable
import probe
from build import TARGET,write

CLI='''import sys,json,os,tempfile
from pathlib import Path
from solution import solve
def main():
 if len(sys.argv)!=3:return 2
 try:
  value=solve(json.loads(Path(sys.argv[1]).read_text('utf-8')))
  out=Path(sys.argv[2]);tmp=None
  try:
   with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=out.parent,delete=False) as f:
    tmp=Path(f.name);json.dump(value,f,sort_keys=True,ensure_ascii=False)
   os.replace(tmp,out)
  finally:
   if tmp and tmp.exists():tmp.unlink()
  return 0
 except (ValueError,OSError,KeyError,TypeError):return 1
if __name__=='__main__':sys.exit(main())
'''
RELEASE='''import sys,json,os,tempfile
from pathlib import Path
def build_manifest(source_dir,out_file):
 data=release(source_dir);out=Path(out_file);tmp=None
 try:
  with tempfile.NamedTemporaryFile(mode='w',dir=out.parent,delete=False) as f:
   tmp=Path(f.name);json.dump(data,f,sort_keys=True)
  os.replace(tmp,out)
 finally:
  if tmp and tmp.exists():tmp.unlink()
 return data
def verify_manifest(source_dir,out_file):
 try:
  m=json.loads(Path(out_file).read_text());return m==release(source_dir)
 except (ValueError,OSError):return False
if __name__=='__main__':
 if len(sys.argv)!=4 or sys.argv[1] not in ('build','verify','update'):sys.exit(2)
 try:
  if sys.argv[1]=='verify':sys.exit(0 if verify_manifest(*sys.argv[2:]) else 1)
  build_manifest(*sys.argv[2:]);sys.exit(0)
 except (ValueError,OSError):sys.exit(1)
'''
SPECIAL_CLI='''import argparse,json,sys,os,tempfile
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument('id');p.add_argument('--format',choices=['text','json'],default='text');p.add_argument('--batch',action='store_true');p.add_argument('--output')
 a=p.parse_args();ids=a.id.split(',') if a.batch else [a.id]
 if any(not x for x in ids):return 2
 values={r['id']:r['value'] for r in json.loads(Path(__file__).with_name('data.json').read_text())['items']}
 rows=[{'id':i,'value':values[i]} if i in values else {'error':'missing','id':i} for i in ids]
 code=int(any('error' in r for r in rows))
 if a.format=='json':out=json.dumps(rows if a.batch else rows[0])+'\\n';err=''
 else:out=''.join(f'{r["id"]}={r["value"]}\\n' for r in rows if 'value' in r);err=''.join(f'missing: {r["id"]}\\n' for r in rows if 'error' in r)
 if a.output and code==0:
  target=Path(a.output)
  with tempfile.NamedTemporaryFile(mode='w',dir=target.parent,delete=False) as f:f.write(out);name=f.name
  os.replace(name,target);out=''
 sys.stdout.write(out);sys.stderr.write(err);return code
if __name__=='__main__':sys.exit(main())
'''

def golden_tests(key):
    inputs=[]
    if key=='parser':
        inputs=['','a=1','x=true','x=false','x=null','x=900719925474099312345','x="a=b"','é=1\né=2','a=1\na=2','a=1\nbad','x=NaN','x=Infinity','=1','x={bad','a=1\nb=2\nb=3']
    else:
        for units in (0,1,9,10,11,19,20,21,22,True,-1):
            for exempt in (True,False):
                for discount in (0,50,1500):inputs.append({'lines':[{'id':'a','units':units,'exempt':exempt}],'discount_cents':discount})
        inputs.extend([{'lines':[]},{'lines':[{'id':'a','units':1,'exempt':False}]*2},{'lines':[{'id':'b','units':2,'exempt':False},{'id':'a','units':1,'exempt':True}],'discount_cents':150}])
        basic={'lines':[{'id':'a','units':1,'exempt':False}]}
        for rs in ([{'refund_id':'r','line_id':'a','cents':30}]*2,[{'refund_id':'r','line_id':'a','cents':30},{'refund_id':'s','line_id':'a','cents':90}],[{'refund_id':'r','line_id':'a','cents':30},{'refund_id':'r','line_id':'a','cents':31}],[{'refund_id':'r','line_id':'a','cents':30}]):inputs.append({**basic,'refunds':rs})
    function='parse' if key=='parser' else 'solve';module='parser' if key=='parser' else 'calculator'
    out=f'import pytest\nfrom {module} import {function}\n'
    out+='def types(x):\n if isinstance(x,dict):return {k:types(v) for k,v in x.items()}\n if isinstance(x,list):return [types(v) for v in x]\n return type(x).__name__\n'
    for i,data in enumerate(inputs):
        try:
            value=ref.SOLVERS[key](copy.deepcopy(data))
            def types(x):
                if isinstance(x,dict):return {k:types(v) for k,v in x.items()}
                if isinstance(x,list):return [types(v) for v in x]
                return type(x).__name__
            out+=f'def test_{i}():\n value={function}({data!r})\n assert value=={value!r}\n assert types(value)=={types(value)!r}\n'
        except ValueError as e:
            out+=f'def test_{i}():\n with pytest.raises(ValueError) as e:{function}({data!r})\n'
            if key=='parser':out+=f' assert str(e.value)=={str(e)!r}\n'
    return out

def gold(cid,spec,folder):
    source=TARGET/'fixtures'/cid
    shutil.copytree(source,folder,dirs_exist_ok=True)
    key=spec['key']
    if key in ('transaction','policy','memory'):return
    if key=='authorization':write(folder/'final.json',spec['answer']);return
    if key=='metrics':write(folder/'metrics.json',ref.metrics(spec['sample']));return
    if key in ('parser','invoice'):
        write(folder/'tests'/('test_parser.py' if key=='parser' else 'test_calculator.py'),golden_tests(key));return
    if key in ('worker','gateway','batch','upgrade','extract'):
        write(folder/'engine.py','import copy,hashlib,json,os\nfrom pathlib import Path\n'+inspect.getsource(durable.load)+'\n'+inspect.getsource(durable.save)+'\n'+(inspect.getsource(durable.service_op)+'\n' if key=='gateway' else '')+inspect.getsource(getattr(durable,key))+f'\nexecute={key}\n');return
    if key=='release':write(folder/'release.py','import hashlib\nfrom pathlib import Path\n'+inspect.getsource(durable.release)+'\n'+RELEASE);return
    if key=='cli':write(folder/'cli.py',SPECIAL_CLI);return
    write(folder/'solution.py',Path(ref.__file__).read_text('utf-8')+f'\nsolve={ref.SOLVERS[key].__name__}\n')
    write(folder/'cli.py',CLI)
    if key=='migration':write(folder/'package/transform.py','from solution import solve as transform\n')
    if key=='protocol':
        write(folder/'api.py','from solution import solve as respond\n');write(folder/'batch_api.py','from solution import solve as respond_batch\n')

def run_probe(cid,workspace):
    args=['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','128','--memory','512m','--cpus','1','--tmpfs','/tmp:rw,nosuid,size=128m',
          '--mount',f'type=bind,source={workspace.resolve()},target=/workspace,readonly',
          '--mount',f'type=bind,source={Path(__file__).parent.resolve()},target=/oracle,readonly',
          '--mount',f'type=bind,source={TARGET.resolve()},target=/spec,readonly',
          '-w','/workspace','-e','PYTHONDONTWRITEBYTECODE=1','miniclaw-runtime:py313-bench','python','/oracle/probe.py',cid,'/spec/judge-spec.json']
    p=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=240)
    if p.returncode: return {'case_id':cid,'error':p.stderr[-3000:]}
    try:return json.loads(p.stdout)
    except Exception:return {'case_id':cid,'error':p.stdout[-1000:]+p.stderr[-2000:]}

def main():
    from concurrent.futures import ThreadPoolExecutor
    specs=json.loads((TARGET/'judge-spec.json').read_text('utf-8'));out=ROOT/'.aster/evals'/TARGET.name/'offline'
    out.mkdir(parents=True,exist_ok=True)
    selected=set(sys.argv[1:]) if len(sys.argv)>1 else set(specs)
    for cid,spec in specs.items():
        if cid in selected: gold(cid,spec,out/'gold'/cid)
    def one(cid):
        result=run_probe(cid,out/'gold'/cid);write(out/'reference-evidence'/f'{cid}.json',result)
        obs=result.get('observations',[]);ok=bool(obs) and all(r.get('matches') is not False and 'error' not in r for r in obs)
        if specs[cid].get('durable'):
            # Agreement between two implementations is insufficient: successful recovery must occur.
            expected_success=[r for r in obs if r['name'].endswith('-return') and 'value' in r.get('expected',{})]
            ok=ok and len(expected_success)>=2
        print(json.dumps({'case':cid,'reference_ok':ok,'checks':len(obs)},ensure_ascii=False),flush=True)
        return {'id':cid,'reference_ok':ok,'checks':len(obs),'evidence':str(out/'reference-evidence'/f'{cid}.json')}
    with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(one,[cid for cid in specs if cid in selected]))
    if selected!=set(specs):
        previous=json.loads((out/'validation.json').read_text('utf-8'))['reference_results']
        results += [r for r in previous if r['id'] not in selected]
    write(out/'validation.json',{'reference_results':results,'ready':all(r['reference_ok'] for r in results)})
    if not all(r['reference_ok'] for r in results):sys.exit(1)

if __name__=='__main__':main()
