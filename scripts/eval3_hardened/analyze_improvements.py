"""Read-only failure and request-context aggregates for the improvement plan."""
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
out = Path(json.loads((ROOT/'.aster/evals/eval3-hardened-r1/active-run.json').read_text('utf-8'))['directory'])
report = json.loads((out/'program-reviewed.json').read_text('utf-8'))
all_buckets = collections.Counter()
for case in report['cases']:
    cid = case['id']
    failed = [(c.get('name') or c.get('options', {}).get('name') or c['type'], c.get('options', {}).get('max')) for c in case['checks'] if not c['passed'] and c.get('required', True)]
    buckets = collections.Counter()
    phases = collections.Counter()
    for p in (out/'run/cases'/cid/'attempt-001/workspace/.aster/eval-sessions').rglob('trace.jsonl'):
        for line in p.read_text('utf-8').splitlines():
            e = json.loads(line)
            if e['type'] != 'model.request':
                continue
            breakdown = e['data'].get('token_accounting', {}).get('estimated_input', {})
            # Schema recorded by frozen trace model client; never print message contents.
            for k,v in breakdown.get('by_source', {}).items():
                if isinstance(v, (int,float)):
                    buckets[k] += v
            phases[e['data'].get('purpose', 'unknown')] += e['data'].get('usage', {}).get('input_tokens', 0)
    all_buckets.update(buckets)
    print(json.dumps({'id':cid,'failed_checks':failed,'estimated_buckets':dict(buckets),'input_by_stage':dict(phases)}))
print(json.dumps({'all_estimated_buckets':dict(all_buckets)}))
