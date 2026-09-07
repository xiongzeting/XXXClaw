"""Supplementary requirement probe, kept separate from frozen-score correction."""
import json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
code='''import json,sys,tempfile,subprocess
from pathlib import Path
with tempfile.TemporaryDirectory() as d:
 p=Path(d);e=p/'e';s=p/'s';r=p/'r'
 events=[{'seq':1,'op':'add','id':'a'},{'seq':2,'op':'add','id':'b'},{'seq':3,'op':'remove','id':'a'},{'seq':4,'op':'add','id':'a'}]
 e.write_text(''.join(json.dumps(x)+'\\n' for x in events))
 result=subprocess.run([sys.executable,'/workspace/queue_tool.py','rebuild',str(e),str(s),str(r)],capture_output=True,text=True)
 value=json.loads(s.read_text()) if s.exists() else None
 actual=value.get('active',value.get('tasks')) if isinstance(value,dict) else value
 print(json.dumps({'exit':result.returncode,'actual':actual,'expected':['a','b'],'passed':actual==['a','b']}))
'''
rows=[]
for version,folder in [('v1','boundary-campaign-v1'),('v9','boundary-full-v9-jobs10')]:
 workspace=ROOT/'.aster/evals'/folder/'run/cases/boundary_v1_queue_rebuild_and_reconcile/attempt-001/workspace'
 command=['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp:rw,noexec,nosuid,size=16m','--mount',f'type=bind,source={workspace},target=/workspace,readonly','-w','/workspace','-e','PYTHONDONTWRITEBYTECODE=1','miniclaw-runtime:py313-bench','python','-c',code]
 p=subprocess.run(command,capture_output=True,text=True,timeout=30)
 assert p.returncode==0,p.stderr
 rows.append({'version':version,**json.loads(p.stdout)})
path=ROOT/'.aster/evals/boundary-full-v9-review/queue-supplementary.json'
path.write_text(json.dumps(rows,indent=2),encoding='utf-8');print(path.read_text())
