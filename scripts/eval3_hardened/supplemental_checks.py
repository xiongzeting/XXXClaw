"""Post-review probes for published requirements; no candidate or frozen artifact edits."""
import hashlib
import json
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(json.loads((ROOT/'.aster/evals/eval3-hardened-r1/active-run.json').read_text('utf-8'))['directory'])
DRIVER = r'''
import json,sys,tempfile,subprocess,copy,io,builtins
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,'/workspace')
key=sys.argv[1]; rows=[]
def result(name,expected,actual):rows.append(dict(name=name,expected=expected,observed=actual,matches=expected==actual))
def call(f,*args):
 try:return {'value':f(*args)}
 except Exception as e:return {'error':type(e).__name__}
if key=='shipping':
 from solution import solve
 p={'id':'a','weight_g':1001,'zone':'local','fragile':False}
 a=[{'amendment_id':'later','revision':2,'package_id':'a','set':{'free':False}}, {'amendment_id':'earlier','revision':1,'package_id':'a','set':{'free':True}}]
 for i,am in enumerate((a,list(reversed(a)))):
  result('free-amendment-order-'+str(i),{'value':[{'id':'a','shipping_cents':700}]},call(solve,{'packages':[p],'amendments':am}))
elif key=='release':
 import release
 with tempfile.TemporaryDirectory() as d:
  root=Path(d);out=root/'out.json';out.write_bytes(b'KEEP')
  for op in ('build','update'):
   p=subprocess.run([sys.executable,'/workspace/release.py',op,str(root/'missing'),str(out)],capture_output=True,text=True)
   result('missing-source-'+op,{'exit':1,'output':'KEEP'},{'exit':p.returncode,'output':out.read_text()})
  src=root/'src';src.mkdir();(src/'a.py').write_text('a');hits=[]
  old_open=builtins.open;old_io=io.open;old_bytes=Path.read_bytes
  def blocked(file,*args,**kw):
   if not isinstance(file,int) and Path(file)==src/'a.py':hits.append(str(file));raise OSError('read fault reached')
   return old_open(file,*args,**kw)
  def blocked_io(file,*args,**kw):
   if not isinstance(file,int) and Path(file)==src/'a.py':hits.append(str(file));raise OSError('read fault reached')
   return old_io(file,*args,**kw)
  def blocked_bytes(file):
   if file==src/'a.py':hits.append(str(file));raise OSError('read fault reached')
   return old_bytes(file)
  with patch('builtins.open',blocked),patch('io.open',blocked_io),patch.object(Path,'read_bytes',blocked_bytes):
   observed=call(release.build_manifest,str(src),str(out))
  result('actual-read-fault-preserves-output',{'fault_reached':True,'output':'KEEP'},{'fault_reached':bool(hits),'output':out.read_text()})
elif key=='report':
 from solution import solve
 base={'tenant':'t','id':'x','revision':2,'time':'2026-09-01T12:00:00+00:00','amount':10}
 data={'events':[base,dict(base,revision=1,amount=3),dict(base,revision=1,amount=4)],'timezones':{'t':'UTC'},'queries':[{'query_id':'q','tenant':'t','start':'2026-09-01','end':'2026-09-02'}]}
 result('shadowed-revision-conflict',{'error':'ValueError'},call(solve,data))
elif key=='extract':
 from engine import execute
 with tempfile.TemporaryDirectory() as d:
  entries=[dict(id='a',path='a.txt',type='file',content='A'),dict(id='before_a',path='b.txt',type='file',content='B')]
  result('entry-id-is-not-fault-alias',{'error':'OSError'},call(execute,d,entries,'before_a'))
  result('rollback-leaves-no-new-files',[],sorted(p.name for p in Path(d).iterdir() if p.is_file()))
print(json.dumps({'observations':rows,'outcome_status':'assistant_review_required'}))
'''
MAPPING={'shipping_caps_r3':'shipping','diagnose_and_patch_release_r3':'release','cross_file_regression_triage':'report','path_traversal_archive_extract':'extract'}
def main():
    dest=OUT/'supplemental-outcome-evidence';dest.mkdir(exist_ok=True)
    (dest/'probe-source.py').write_text(DRIVER,'utf-8')
    notes={'purpose':'Verify issues found by human/assistant code review against already public requirements; do not change initial oracle or candidate.',
           'source_sha256':hashlib.sha256(DRIVER.encode()).hexdigest(),
           'requirements':{'shipping':'Replay all amendment fields by revision/id; base 700 comes from the public sample, not unspecified remote pricing.',
                           'release':'Runtime failure exits 1; actual read failure preserves previous output. Original Path.read_bytes injection did not reach candidate Path.open.',
                           'report':'Same tenant/id/revision with conflicting contents is invalid even if a higher revision was seen.',
                           'extract':'fault equals entry ID: inject OSError after that entry is written; no reserved ID prefixes were specified.'}}
    (dest/'provenance.json').write_text(json.dumps(notes,ensure_ascii=False,indent=2),'utf-8')
    for cid,key in MAPPING.items():
        work=OUT/'run/cases'/cid/'attempt-001/workspace'
        args=['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','512m','--pids-limit','64','--tmpfs','/tmp:rw,nosuid,size=32m','--mount',f'type=bind,source={work},target=/workspace,readonly','-w','/workspace','-e','PYTHONDONTWRITEBYTECODE=1','miniclaw-runtime:py313-bench','python','-c',DRIVER,key]
        proc=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=60)
        packet=json.loads(proc.stdout) if proc.returncode==0 else {'probe_error':proc.stderr}
        packet['case_id']=cid
        (dest/(cid+'.json')).write_text(json.dumps(packet,ensure_ascii=False,indent=2),'utf-8')
        print(json.dumps(packet,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
