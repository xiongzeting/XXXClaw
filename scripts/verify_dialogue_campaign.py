"""Verify suite contracts and regrade validation without changing raw reports."""
import ast
import hashlib
import json
from pathlib import Path

from MiniClaw.evaluation.models import load_eval_suite
from MiniClaw.evaluation.runner import evaluate_check

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / '.aster/evals/expansion-20260905'
manifest = json.loads((ROOT / 'evals/dialogue-campaign-manifest.json').read_text(encoding='utf-8'))
for name, expected in manifest['runtime_source_hashes'].items():
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
for name, count in [('dialogue-all-development.json', 112), ('dialogue-failures.json', 10), ('portfolio-v2.json', 32)]:
    suite = load_eval_suite(ROOT / 'evals' / name)
    assert len(suite.cases) == count, name
for path in (ROOT / 'scripts').glob('*dialogue*.py'):
    ast.parse(path.read_text(encoding='utf-8'))

suite = load_eval_suite(ROOT / 'evals/dialogue-replacements-validation.json')
specs = {c.id: c for c in suite.cases}
raw = json.loads((RUNS / 'replacement-validation/report.json').read_text(encoding='utf-8'))
reviewed = []
for case in raw['cases']:
    attempts = []
    for attempt in case['attempts']:
        checks = [evaluate_check(check, Path(attempt['workspace']), Path(attempt['workspace']).parent,
                  attempt['phases'], {}, metrics=attempt.get('metrics'),
                  workspace_changes=attempt.get('workspace_changes')) for check in specs[case['id']].checks]
        attempts.append({'passed': not attempt.get('error') and all(c['passed'] for c in checks if c.get('required', True)), 'checks': checks})
    reviewed.append({'id': case['id'], 'original_passed': case['passed'],
                     'reviewed_passed': all(a['passed'] for a in attempts), 'attempts': attempts})
assert len(reviewed) == 6 and all(c['reviewed_passed'] for c in reviewed)
(RUNS / 'replacement-validation/reviewed-results.json').write_text(
    json.dumps({'source': 'report.json', 'policy': 'Original reports unchanged; regrade corrected oracle only',
                'reviewed_passed': 6, 'cases': reviewed}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('Validated suite counts, script syntax, source hashes; replacement review 6/6.')
