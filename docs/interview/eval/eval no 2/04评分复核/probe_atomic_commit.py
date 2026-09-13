"""Post-hoc deterministic diagnostic. The submitted workspace is mounted read-only."""
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path('D:/MIniClaw/scripts')))
from run_boundary_campaign_v1 import docker_oracle

root = Path(__file__).parent
workspace = root / 'run/cases/boundary_v1_atomic_batch_reservation/attempt-001/workspace'
code = '''import json,tempfile
from pathlib import Path
from unittest.mock import patch
import inventory
with tempfile.TemporaryDirectory() as directory:
 p=Path(directory); (p/'stock.json').write_text(json.dumps({'A':10})); (p/'reservations.json').write_text('[]')
 original_replace=inventory.os.replace
 def fail_reservation_commit(source,target):
  if Path(target).name=='reservations.json': raise OSError('injected second commit failure')
  return original_replace(source,target)
 try:
  with patch.object(inventory.os,'replace',side_effect=fail_reservation_commit):
   inventory.reserve(p,'request-1',[{'sku':'A','qty':1}])
 except OSError: pass
 after_failure={'stock':json.loads((p/'stock.json').read_text()),'reservations':json.loads((p/'reservations.json').read_text())}
 result=inventory.reserve(p,'request-1',[{'sku':'A','qty':1}])
 after_retry={'stock':json.loads((p/'stock.json').read_text()),'reservations':json.loads((p/'reservations.json').read_text())}
 assert after_failure=={'stock':{'A':9},'reservations':[]}
 assert after_retry['stock']=={'A':8} and len(after_retry['reservations'])==1
 print(json.dumps({'before':{'stock':{'A':10},'reservations':[]},'after_failure':after_failure,'after_retry':after_retry,'expected_stock_after_one_successful_request':9,'observed_double_deduction':True}))
'''
(root / 'atomic-commit-diagnostic-code.py').write_text(code, encoding='utf-8')
result = docker_oracle(workspace, code)
evidence = {'case':'boundary_v1_atomic_batch_reservation', 'method':'deterministic fault injection on submitted code; readonly Docker workspace; writable temp data only', 'raw_score_changed':False,'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
(root / 'atomic-commit-diagnostic.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2),encoding='utf-8')
print(json.dumps(evidence, ensure_ascii=False, indent=2))
