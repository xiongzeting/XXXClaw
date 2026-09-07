"""Read-only compact inspection of the completed batch, never model execution."""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / '.aster/evals/hard-campaign-v1'
assert (R / 'completion.json').exists()
raw = json.loads((R / 'run/report.json').read_text(encoding='utf-8'))
for c in raw['cases']:
    a = c['attempts'][0]
    print(c['id'], 'PASS' if c['passed'] else 'FAIL')
    print('failed:', [(x['dimension'], x['detail'][:180]) for x in a['checks'] if x.get('required', True) and not x['passed']])
    if c['category'] != 'safety':
        continue
    print('changes:', a['workspace_changes'])
    seen_traces = set()
    for name, p in a['phases'].items():
        print(name, 'final:', p['final_text'][:1200])
        trace = Path(p['trace_path'])
        if not trace.is_absolute():
            trace = ROOT / trace
        if trace in seen_traces:
            continue
        seen_traces.add(trace)
        for line in trace.read_text(encoding='utf-8').splitlines():
            e = json.loads(line)
            d = e.get('data', {})
            if e.get('type') == 'tool.call' and d.get('tool_name') in ('write', 'edit'):
                print('mutation:', d.get('status'), d.get('arguments', {}).get('path'))
    ws = R / 'run/cases' / c['id'] / 'attempt-001/workspace'
    for p in ws.glob('*.json'):
        print('artifact:', p.name, p.read_text(encoding='utf-8-sig')[:1600])
