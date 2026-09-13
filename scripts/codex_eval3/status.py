import collections
import json
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parents[2]
pointer=json.loads((ROOT/'.aster/evals/codex-eval3/active-run.json').read_text('utf-8'))
out=Path(pointer['directory'])
states=[json.loads(p.read_text('utf-8')) for p in (out/'cases').glob('*/status.json')]
phases=[json.loads(p.read_text('utf-8')) for p in (out/'cases').glob('*/turn*/result.json')]
timings=[json.loads(p.read_text('utf-8')) for p in (out/'requests').glob('*/*.timing.json')]
execution=json.loads((out/'execution.json').read_text('utf-8'))
status=json.loads((out/'run-status.json').read_text('utf-8')) if (out/'run-status.json').exists() else {}
usage=[t['usage'] for t in timings if t.get('usage')]
print(json.dumps({'run':out.name,'states':dict(collections.Counter(s['state'] for s in states)),
 'phase_positions':dict(collections.Counter(s.get('phase','done') for s in states)),
 'phases_completed':sum(p['completed'] for p in phases),'phases_interrupted':sum(not p['completed'] for p in phases),
 'http_requests_finished':len(timings),'usage_missing':sum(t.get('usage') is None for t in timings),
 'observed_tokens':sum(u.get('total_tokens',0) for u in usage),
 'elapsed_seconds':round(status.get('ended_at',time.time())-execution['started_at'],1),'state':status.get('state')}))
