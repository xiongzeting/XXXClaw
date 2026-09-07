"""Read archived observations through the new parser, without commands or rescoring Eval."""
import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from MiniClaw.coding_agent.assistant.verification import decode_verification, observation_value
from MiniClaw.coding_agent.assistant.acceptance import compare
from MiniClaw.coding_agent.tools.base import ToolResult


def main():
    report = json.loads((ROOT/'.aster/evals/efficiency-revision-v5/final-comparison.json').read_text(encoding='utf-8'))
    cases = []; totals = Counter()
    for case in report['cases']:
        rows = []; counts = Counter()
        trace = Path(case['v5_trace']['trace_path'])
        with trace.open(encoding='utf-8') as stream:
            for line_no, line in enumerate(stream, 1):
                event = json.loads(line)
                if event['type'] != 'tool.call': continue
                data = event.get('data') or {}; details = data.get('details') or {}
                plan = details.get('verification_plan')
                if not plan: continue
                old_error = (details.get('task_verification') or {}).get('error')
                wrapper_pointer = any(str(c.get('pointer')).startswith('/observations/')
                    for s in plan['checks'] for c in s['comparisons'])
                result = ToolResult('', details=copy.deepcopy(details))
                # is_error can reflect the old parser, so use captured execution status here.
                errors = []; mismatches = []
                try:
                    if details.get('exit_code') != 0 or details.get('not_started'):
                        raise ValueError('Execution not successful')
                    payload, _ = decode_verification(result, 'observations')
                    for spec in plan['checks']:
                        for c in spec['comparisons']:
                            try:
                                actual = observation_value(payload['observations'], c, result.details.setdefault('verification_normalizations', []))
                                if not compare(actual, c['expected'], c.get('operator', 'equals')):
                                    mismatches.append({'criterion_id':spec['criterion_id'], 'check_id':c['check_id']})
                            except (ValueError, TypeError, KeyError) as exc:
                                errors.append(str(exc))
                except (ValueError, TypeError, KeyError) as exc:
                    errors.append(str(exc))
                status = 'protocol_invalid' if errors else 'comparison_mismatch' if mismatches else 'comparisons_match'
                counts['structured_calls'] += 1
                counts[status] += 1
                if old_error and not errors: counts['old_error_now_parseable'] += 1
                if wrapper_pointer:
                    counts['wrapper_pointer_calls'] += 1
                    counts['wrapper_pointer_'+status] += 1
                rows.append({'line':line_no, 'capture_sha256':hashlib.sha256(line.encode()).hexdigest(),
                    'original_verification_error':old_error, 'wrapper_pointer':wrapper_pointer, 'replay_status':status,
                    'errors':errors, 'mismatched_checks':mismatches})
        totals.update(counts)
        cases.append({'id':case['id'], 'name':case['name'], 'trace':str(trace), 'counts':dict(counts), 'calls':rows})
    output = ROOT/'.aster/evals/efficiency-revision-v6-parser-replay.json'
    output.write_text(json.dumps({'scope':'Archived parser/comparison replay only. No artifact freshness, requirement state, external oracle or model rerun; original grades unchanged.',
        'model_requests':0, 'commands_executed':0, 'counts':dict(totals), 'cases':cases}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(totals), ensure_ascii=False))


if __name__ == '__main__': main()
