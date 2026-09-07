"""Finalize one malformed duplicate-id input before any model execution."""
import copy,json,hashlib
from pathlib import Path
from build_hard_verified_v1 import LOCAL,function_oracle
from build_dialogue_campaign import hidden_test
from run_hard_campaign_v1 import hashes

ROOT=Path(__file__).resolve().parents[1]; E=ROOT/'evals'; R=ROOT/'.aster/evals/hard-campaign-v1'
assert not (R/'execution.json').exists() and not (R/'run').exists(), 'No post-run amendments permitted'
spec=next(s for s in LOCAL if s['key']=='segmented_router')
replacement=hidden_test(function_oracle(spec))
old=json.loads((R/'freeze.json').read_text(encoding='utf-8'))
(R/'preflight-draft-freeze.json').write_text(json.dumps(old,indent=2)+'\n',encoding='utf-8')
paths=[E/'hard-memory-v1.json',E/'hard-campaign-v1.json',E/'development-challenge-v3.json']
for p in paths:
 data=json.loads(p.read_text(encoding='utf-8'))
 for c in data['cases']:
  if c['id']=='hard_v1_segmented_router': c['checks'][0]=copy.deepcopy(replacement)
 p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 (R/'snapshot/evals'/p.name).write_bytes(p.read_bytes())
old['eval_hashes']=hashes(R/'snapshot/evals')
old['preflight_amendment']='Before first model execution, duplicate-id negative input now supplies all other mandatory rule fields. Old hash manifest preserved separately.'
for p in (R/'freeze.json',E/'hard-campaign-freeze-v1.json'):
 p.write_text(json.dumps(old,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Final preflight amendment recorded; no model runs have started')
