"""Summarize completed cases only; never mix partial usage into final case totals."""
import json
from pathlib import Path
from report_efficiency_v4_final import metrics

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'.aster/evals/efficiency-revision-v7'
rows = []
running = []
for folder in sorted((OUT/'run/cases').iterdir()):
    if not folder.is_dir():
        continue
    result = folder/'result.json'
    if not result.exists():
        running.append(folder.name)
        continue
    case = json.loads(result.read_text(encoding='utf-8'))
    row = {'id':folder.name, **metrics(case), 'verification_error_feedback':0}
    trace = folder/'attempt-001/workspace/.aster/eval-sessions/shared/trace.jsonl'
    for line in trace.read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        if event['type'] == 'tool.call':
            details = (event.get('data') or {}).get('details') or {}
            row['verification_error_feedback'] += bool((details.get('task_verification') or {}).get('error'))
    rows.append(row)
totals = {key:sum(row[key] for row in rows) for key in (
    'total_tokens','input_tokens','cached_tokens','uncached_input','verification_error_feedback','paused_runs')}
totals['cache_rate_pct'] = round(100*totals['cached_tokens']/totals['input_tokens'], 2) if totals['input_tokens'] else None
totals['dimension_pass'] = {dim:sum(row['dimension_pass'][dim] for row in rows)
    for dim in ('outcome','process','efficiency','safety','reliability')}
print(json.dumps({'completed':len(rows),'running':running,'totals_completed_cases_only':totals}, ensure_ascii=False, indent=2))
