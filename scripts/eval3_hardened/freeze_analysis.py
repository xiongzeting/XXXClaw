"""Record postprocessing rule provenance separately from the runtime freeze."""
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
out=Path(json.loads((ROOT/'.aster/evals/eval3-hardened-r1/active-run.json').read_text('utf-8'))['directory'])
paths=['observe.py','collect_evidence.py','infer_price.py']
folder=out/'analysis-code';folder.mkdir(exist_ok=True)
hashes={}
for name in paths:
    source=Path(__file__).parent/name;raw=source.read_bytes();(folder/name).write_bytes(raw);hashes[name]=hashlib.sha256(raw).hexdigest()
record={'timestamp':datetime.now(timezone.utc).isoformat(),'files':hashes,'provenance':'Postprocessing implementation created during model run, before assistant outcome review. Runtime checks, candidate tasks and behavioral probes were frozen before execution; additions implement published phase authorization, actual verification, and effective-time rules. Original program scores retained separately.','pricing':'user supplied billing inference, observation only'}
(out/'analysis-rule-provenance.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(record))
