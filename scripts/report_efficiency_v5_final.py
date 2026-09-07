"""Read completed frozen runs, preserving original scores and exact trace paths."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from report_efficiency_v4_final import BASE, NAMES, metrics, pct, read

OUT = BASE / 'efficiency-revision-v5'


def audit(case_id, version):
    path = BASE / f'efficiency-revision-{version}/run/cases' / case_id / 'attempt-001/workspace/.aster/eval-sessions/shared/trace.jsonl'
    counts = Counter()
    prefixes = Counter()
    verification_errors = Counter()
    tool_errors = Counter()
    phases = {}
    examples = []
    previous = None
    for line_no, line in enumerate(path.open(encoding='utf-8'), 1):
        e = json.loads(line)
        d = e.get('data') or {}
        run = e.get('run_id') or 'unknown'
        phase = phases.setdefault(run, {'tokens': 0, 'requests': 0, 'tools': 0, 'verification_errors': 0})
        typ = e['type']
        if typ == 'run.started':
            phase['request'] = d.get('request', '')[:220]
        if typ == 'run.completed':
            phase['completion'] = {k: d.get(k) for k in ('status', 'pause_reason', 'error', 'duration_ms')}
        if typ == 'model.request':
            counts['model_seconds_sum'] += d.get('duration_ms', 0) / 1000
            counts['model_retries'] += d.get('retries', 0)
            counts['model_error_records'] += d.get('status') == 'error'
            phase['tokens'] += d.get('usage', {}).get('total_tokens', 0)
            phase['requests'] += 1
            if d.get('purpose') == 'agent':
                context = d.get('context') or {}
                meta = context.get('metadata') or {}
                prefix = meta.get('request_prefix') or {}
                prefixes[prefix.get('first_changed_component', 'unrecorded')] += 1
                messages = context.get('messages') or []
                if previous and previous[0] == run and messages:
                    old_messages = previous[1]
                    counts['same_phase_request_pairs'] += 1
                    counts['exact_old_message_prefix_preserved'] += messages[:len(old_messages)] == old_messages
                previous = (run, messages)
        if typ == 'tool.call':
            phase['tools'] += 1
            details = d.get('details') or {}
            ver = details.get('task_verification') or {}
            error = ver.get('error')
            counts['tool_or_verification_error_events'] += bool(d.get('status') == 'error' or error)
            if error:
                verification_errors[str(error)] += 1
                phase['verification_errors'] += 1
            if d.get('status') in ('error', 'blocked', 'cancelled'):
                tool_errors[str(d.get('tool_name')) + ': ' + str(d.get('error') or d.get('result') or '')[:420]] += 1
            if details.get('verification_plan'):
                counts['structured_verification_plan_records'] += 1
            if details.get('verification_plan_error'):
                counts['verification_preflight_errors'] += 1
            if (error or d.get('status') == 'error') and len(examples) < 30:
                examples.append({'trace_line': line_no, 'run_id': run, 'tool': d.get('tool_name'),
                                 'result': str(d.get('error') or d.get('result') or '')[:2200], 'verification': ver})
    return {'trace_path': str(path), **dict(counts), 'prefix_changes': dict(prefixes),
            'verification_error_count': sum(verification_errors.values()),
            'verification_errors': dict(verification_errors), 'tool_error_categories': dict(tool_errors),
            'phases': phases, 'error_examples': examples}


def main():
    assert (OUT / 'completion.json').exists(), 'Wait for every case to finish before analysing scores'
    reports = {v: read(BASE / f'efficiency-revision-{v}/run/report.json') for v in ('v2', 'v5')}
    rows = []
    for case in reports['v5']['cases']:
        old = next(c for c in reports['v2']['cases'] if c['id'] == case['id'])
        row = {'id': case['id'], 'name': NAMES[case['id']], 'v2': metrics(old), 'v5': metrics(case)}
        for v, c in [('v2', old), ('v5', case)]:
            row[v + '_trace'] = audit(case['id'], v)
            row[v + '_failed_checks'] = [x for x in c['checks'] if x.get('required', True) and not x['passed']]
            row[v + '_network'] = {k: val for k, val in c['metrics'].items()
                                  if 'network' in k or 'recover' in k or k in ('model_retries', 'delivered_runs', 'undelivered_runs')}
        rows.append(row)
    assert len(rows) == 6
    totals = {}
    for v in reports:
        keys = ['total_tokens', 'input_tokens', 'cached_tokens', 'uncached_input', 'output_tokens',
                'tool_calls', 'tool_errors', 'agent_model_requests', 'paused_runs', 'compactions',
                'compaction_tokens', 'active_wall_seconds']
        t = {k: sum(r[v][k] for r in rows) for k in keys}
        t['dimension_pass'] = {d: sum(r[v]['dimension_pass'][d] for r in rows) for d in rows[0][v]['dimension_pass']}
        t['all_dimensions'] = sum(r[v]['all_dimensions'] for r in rows)
        t['cache_rate_pct'] = round(100 * t['cached_tokens'] / t['input_tokens'], 2)
        for key in ('verification_error_count', 'verification_preflight_errors', 'structured_verification_plan_records', 'tool_or_verification_error_events',
                    'model_seconds_sum', 'model_retries', 'model_error_records'):
            t[key] = sum(r[v + '_trace'].get(key, 0) for r in rows)
        t['batch_elapsed_seconds'] = reports[v]['summary']['elapsed_seconds']
        totals[v] = t
    comparison = {'at': datetime.now(timezone.utc).isoformat(), 'totals': totals, 'cases': rows,
                  'original_report_sha256': {v: hashlib.sha256((BASE / f'efficiency-revision-{v}/run/report.json').read_bytes()).hexdigest() for v in reports},
                  'limits': ['Exposed regression, one attempt per version, unchanged case definitions and budgets.',
                             'v2 two lanes vs v5 six lanes; wall time is not a controlled performance experiment.',
                             'Multiple runtime changes; no isolated causal attribution to cache.',
                             'Preserve raw scoring; classify recovered networking separately.']}
    (OUT / 'final-comparison.json').write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(totals, ensure_ascii=False, indent=2))
    for r in rows:
        print(json.dumps({'name': r['name'], 'v2': r['v2'], 'v5': r['v5'],
                          'v5_verification_errors': r['v5_trace']['verification_errors'],
                          'v5_failed_checks': r['v5_failed_checks'], 'network': r['v5_network']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
