from pathlib import Path
import json
import sys
sys.path.insert(0, 'D:/MIniClaw/scripts')
from boundary_delivery_specs import SPECS
from run_boundary_campaign_v1 import docker_oracle

root=Path(__file__).parent
changes={
 'audit_bundle_integration': [
  ("'audit.py',str(l),str(q),str(o)","'audit.py',str(l),'--policy',str(q),'--bundle',str(o)"),
  ("json.loads(o.read_text())['users']['alice']['read']==1", "json.loads(o.read_text())['by_user']['alice']==1")],
 'diagnose_and_patch_release': [
  ("[x['path'] for x in m['files']]==['a.py','b.py']", "list(m)==['a.py','b.py'] and m['a.py']==hashlib.sha256(b'a').hexdigest() and m['b.py']==hashlib.sha256(b'b').hexdigest()")],
}
rows=[]
for key, replacements in changes.items():
 spec=next(s for s in SPECS if s['key']==key)
 code=spec['oracle']
 for before, after in replacements:
  assert before in code, before
  code=code.replace(before,after)
 name=key+'-diagnostic-oracle.py'
 (root/name).write_text(code,encoding='utf-8')
 result=docker_oracle(root/f'run/cases/boundary_v1_{key}/attempt-001/workspace',code)
 rows.append({'case':'boundary_v1_'+key,'review_label':'oracle_contract_mismatch','raw_score_changed':False,
  'revised_score':None,'diagnostic_code':name,'changes':replacements,'method':'Docker read-only delivered workspace; writable temporary input/output; no model calls',
  'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
(root/'final-contract-diagnostics.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(rows,ensure_ascii=False,indent=2))
