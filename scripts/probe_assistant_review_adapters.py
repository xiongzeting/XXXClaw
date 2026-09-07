"""Read-only evidence probes for legal output/state representations, not judge grades."""
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.aster/evals/assistant-outcome-review-v1-v10'
BATCHES={'v1':'boundary-v1-jobs20-current-judge','v10':'boundary-full-v10-jobs20'}
PROBES={
'boundary_v1_idempotent_charge_recovery':'''import json,tempfile
from pathlib import Path
import charge as m
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/'ledger.json';m.LEDGER=p;p.write_text(json.dumps({'version':1,'charges':[{'order_id':'old','cents':90}]}))
 try:m.charge({},'new',30,True)
 except RuntimeError:raised=True
 else:raised=False
 interrupted=json.loads(p.read_text())
 a=m.charge({},'new',30);b=m.charge({},'new',30);final=json.loads(p.read_text())
 print(json.dumps({'raised':raised,'interrupted':interrupted,'final':final,'receipts':[a,b],
 'passed':raised and a==b and a.get('cents')==30 and [(r['order_id'],r['cents']) for r in final['charges']]==[('old',90),('new',30)]}))
''',
'boundary_v1_audit_bundle_integration':'''import json,tempfile,subprocess,sys
from pathlib import Path
with tempfile.TemporaryDirectory() as d:
 p=Path(d);log=p/'events';policy=p/'policy';out=p/'bundle.json'
 log.write_text(''.join(json.dumps(x)+'\\n' for x in [{'event_id':'e1','user':'bob','action':'read'},{'event_id':'e2','user':'alice','action':'read'}]))
 policy.write_text(json.dumps({'allow':['*:read'],'deny':[]}))
 forms=[[str(log),str(policy),str(out)],[str(log),'--policy',str(policy),'--bundle',str(out)],[str(log),'--policy',str(policy)]]
 for form in forms:
  r=subprocess.run([sys.executable,'/workspace/audit.py',*form],cwd=p,capture_output=True,text=True)
  if r.returncode==0 and out.exists():break
 x=json.loads(out.read_text());users=x.get('users',x.get('by_user'))
 def valid(v):return v==1 or v=={'read':1} or v=={'total_events':1,'actions':{'read':1}}
 print(json.dumps({'exit':r.returncode,'actual':x,'passed':r.returncode==0 and x['version']==1 and x['total_events']==2 and list(users)==['alice','bob'] and all(valid(v) for v in users.values())}))
''',
'boundary_v1_queue_rebuild_and_reconcile':'''import json,tempfile,subprocess,sys
from pathlib import Path
with tempfile.TemporaryDirectory() as d:
 p=Path(d);e=p/'events';s=p/'state';r=p/'report'
 events=[{'seq':1,'op':'add','id':'a'},{'seq':2,'op':'add','id':'b'},{'seq':3,'op':'remove','id':'a'},{'seq':4,'op':'add','id':'a'}]
 e.write_text(''.join(json.dumps(x)+'\\n' for x in events))
 result=subprocess.run([sys.executable,'/workspace/queue_tool.py','rebuild',str(e),str(s),str(r)],capture_output=True,text=True)
 value=json.loads(s.read_text());actual=value.get('active',value.get('tasks')) if isinstance(value,dict) else value
 print(json.dumps({'exit':result.returncode,'actual':actual,'expected':['a','b'],'passed':result.returncode==0 and actual==['a','b']}))
'''}


def main():
 for version,folder in BATCHES.items():
  for case,code in PROBES.items():
   source=ROOT/'.aster/evals'/folder/'run/cases'/case/'result.json'
   dest=OUT/version/(case+'.probe.json')
   if not source.exists() or dest.exists():continue
   workspace=Path(json.loads(source.read_text())['workspace'])
   command=['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL',
     '--security-opt','no-new-privileges','--memory','256m','--pids-limit','64',
     '--tmpfs','/tmp:rw,noexec,nosuid,size=16m','--mount',f'type=bind,source={workspace},target=/workspace,readonly',
     '-w','/workspace','-e','PYTHONDONTWRITEBYTECODE=1','miniclaw-runtime:py313-bench','python','-c',code]
   r=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=40)
   evidence={'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
   dest.parent.mkdir(exist_ok=True);dest.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
   print(version,case,r.stdout or r.stderr)


if __name__=='__main__':main()
