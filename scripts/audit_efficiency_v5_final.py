"""Persist small, source-linked observations from the completed batch."""
import json
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.aster/evals/efficiency-revision-v5'
data = json.loads((OUT / 'final-comparison.json').read_text(encoding='utf-8'))
rows = []
for row in data['cases']:
    counts = Counter(); samples = []; last_request = None; completed = []; calls = []
    for line_no, line in enumerate(Path(row['v5_trace']['trace_path']).open(encoding='utf-8'), 1):
        e = json.loads(line); d = e.get('data') or {}
        if e['type'] == 'model.request' and d.get('purpose') == 'agent':
            last_request = d
        if e['type'] == 'run.completed':
            completed.append({'line': line_no, 'run_id': e.get('run_id'), 'status': d.get('status'),
                              'pause_reason': d.get('pause_reason'), 'final_text': d.get('final_text')})
        if e['type'] != 'tool.call':
            continue
        detail = d.get('details') or {}
        err = (detail.get('task_verification') or {}).get('error') or ''
        plan = detail.get('verification_plan') or {}
        pointers = [c.get('pointer') for p in plan.get('checks', []) for c in p['comparisons']]
        counts['wrong_wrapper_pointer_calls'] += any(str(p).startswith('/observations/') for p in pointers)
        counts['missing_field_feedback'] += 'required field missing' in err
        counts['no_record_feedback'] += 'JSON record, found 0' in err
        if err and len(samples) < 9:
            samples.append({'line': line_no, 'error': err, 'pointers': pointers,
                            'stdout': detail.get('stdout', '')[:1400], 'stderr': detail.get('stderr', '')[:800]})
        if plan and len(calls) < 5:
            calls.append({'line': line_no, 'data_keys': list(d), 'arguments': d.get('arguments'), 'plan': plan})
    item = {'id': row['id'], 'name': row['name'], 'counts': dict(counts), 'examples': samples,
            'run_completions': completed, 'verification_examples': calls}
    if row['id'].endswith('lock_retraction'):
        contexts = (last_request or {}).get('context', {}).get('messages', [])
        excerpts = []
        for i, msg in enumerate(contexts):
            text = json.dumps(msg, ensure_ascii=False)
            for token in ('V1', 'rejected_proposals', 'core=2.5.0'):
                at = text.find(token)
                if at >= 0:
                    excerpts.append({'message': i, 'token': token, 'text': text[max(0, at-200):at+700]})
        item['final_request_context_excerpts'] = excerpts
    rows.append(item)
(OUT / 'cause-audit.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
for r in rows:
    print(r['name'], r['counts'])
    if 'final_request_context_excerpts' in r:
        print(json.dumps(r['final_request_context_excerpts'], ensure_ascii=False))
suite = json.loads((OUT / 'snapshot/evals/boundary-efficiency-v5.json').read_text(encoding='utf-8'))
atomic = next(c for c in suite['cases'] if c['id'].endswith('atomic_batch_reservation'))
print('ATOMIC_PROMPTS', json.dumps(atomic['phases'], ensure_ascii=False))
print('TOOL_KEYS', rows[0]['verification_examples'][0]['data_keys'])
