import json,tempfile
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
