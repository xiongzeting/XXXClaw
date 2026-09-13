import json
from pathlib import Path
import time
ROOT=Path(__file__).resolve().parents[2]
out=Path(json.loads((ROOT/'.aster/evals/eval3-hardened-r1/active-run.json').read_text('utf-8'))['directory'])
suite=json.loads((out/'snapshot/evals/suite.json').read_text('utf-8'))
status=json.loads((out/'run-status.json').read_text('utf-8'))
rows=[]
for c in suite['cases']:
    folder=out/'phase-evidence'/c['id'];finished=list(folder.glob('*/phase.json'))
    complete=(out/'run/cases'/c['id']/'result.json').exists()
    rows.append({'id':c['id'],'phases_finished':len(finished),'phases_total':len(c['phases']),'collected':complete})
print(json.dumps({'state':status['state'],'elapsed_seconds':round(time.time()-status['started_at'],1),'completed_cases':sum(r['collected'] for r in rows),'cases':rows},ensure_ascii=False))
