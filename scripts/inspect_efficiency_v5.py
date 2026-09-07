"""Compact read-only inspection of completed v5 regression evidence."""
import json
import sys
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.aster/evals/efficiency-revision-v5'
comparison = json.loads((OUT / 'final-comparison.json').read_text(encoding='utf-8'))
for row in comparison['cases']:
    if len(sys.argv) > 1 and not any(term in row['id'] for term in sys.argv[1:]):
        continue
    print('\nCASE', row['name'])
    print('V2_FAILURES', [(c['dimension'], c['detail'][:650]) for c in row['v2_failed_checks']])
    print('PHASES', json.dumps(list(row['v5_trace']['phases'].values()), ensure_ascii=False))
    print('PREFIX', {k: row['v5_trace'].get(k) for k in ('prefix_changes', 'same_phase_request_pairs', 'exact_old_message_prefix_preserved')})
    examples = []; counts = Counter(); pauses = []; completions = []
    for n, line in enumerate(Path(row['v5_trace']['trace_path']).open(encoding='utf-8'), 1):
        e = json.loads(line); d = e.get('data') or {}
        if e['type'] == 'tool.call':
            detail = d.get('details') or {}
            err = (detail.get('task_verification') or {}).get('error') or ''
            plan = detail.get('verification_plan') or {}
            pointers = [c.get('pointer') for spec in plan.get('checks', []) for c in spec['comparisons']]
            counts['verification_feedback_errors'] += bool(err)
            counts['with_wrong_observations_prefix'] += any(str(p).startswith('/observations/') for p in pointers)
            counts['missing_field_feedback'] += 'required field missing' in err
            counts['no_record_feedback'] += 'JSON record, found 0' in err
            stdout = detail.get('stdout', '')
            if len(examples) < 5 and err and ('/observations/' in err or 'found 0' in err):
                examples.append({'line': n, 'error': err, 'pointers': pointers, 'stdout': stdout[:1500],
                                 'stderr': detail.get('stderr', '')[:600], 'result': str(d.get('result', ''))[:400]})
        if e['type'] == 'run.completed':
            completions.append({'line': n, 'data': {k: d.get(k) for k in ('status', 'pause_reason', 'error', 'final_text')}})
        if 'pause' in e['type']:
            pauses.append({'line': n, 'type': e['type'], 'data': d})
    print('COUNTS', dict(counts))
    print('EXAMPLES', json.dumps(examples, ensure_ascii=False))
    print('PAUSES', json.dumps(pauses, ensure_ascii=False))
    print('COMPLETION', json.dumps(completions[-2:], ensure_ascii=False)[:6000])
    if row['id'].endswith('lock_retraction'):
        suite = json.loads((OUT / 'snapshot/evals/boundary-efficiency-v5.json').read_text(encoding='utf-8'))
        case = next(c for c in suite['cases'] if c['id'] == row['id'])
        print('PROMPT_EXCERPTS', [(p['id'], p['prompt'][:1000], p['prompt'][-1300:]) for p in case['phases']])
