"""Offline verification of coverage and evidence; record source baseline drift."""
import ast
import hashlib
import json
from collections import Counter
from pathlib import Path

from MiniClaw.evaluation.models import load_eval_suite

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'evals'
R = ROOT / '.aster/evals/three-split-v2'
DIMENSIONS = {'outcome', 'process', 'efficiency', 'safety', 'reliability'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main():
    files = {'development': 'measured-development-v2.json',
             'retained': 'retained-release-v2.json', 'test': 'test-release-v2.json'}
    seen = set()
    counts = {}
    for split, name in files.items():
        suite = load_eval_suite(E / name)
        categories = Counter(c.category for c in suite.cases)
        assert set(categories) == {'compression', 'recall', 'tools', 'safety', 'completion'}
        assert min(categories.values()) >= (8 if split == 'development' else 5)
        counts[split] = dict(categories)
        for case in suite.cases:
            assert case.id not in seen, case.id
            seen.add(case.id)
            assert DIMENSIONS <= {c.dimension for c in case.checks if c.required}, case.id
            assert not case.fixture or (case.fixture_root / case.fixture).is_dir(), case.id
            for check in case.checks:
                if check.dimension in {'efficiency', 'reliability'} and check.type == 'metric':
                    assert check.options.get('min') != 0, case.id
    assert len(seen) == 96
    release = read(E / 'reviewed-split-release-manifest.json')
    for name, expected in release['release_hashes'].items():
        assert hashlib.sha256((E / name).read_bytes()).hexdigest() == expected, name
    source = read(E / 'capability-campaign-manifest.json')['source_hashes']
    source_drift = []
    current_hashes = {}
    for name, expected in source.items():
        path = ROOT / name
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        current_hashes[name] = actual
        if actual != expected:
            source_drift.append({'path': name, 'baseline_sha256': expected,
                                 'current_sha256': actual,
                                 'mtime_unix': path.stat().st_mtime if path.exists() else None})
    evidence = read(R / 'failure-export-summary.json')
    for name, key in [('split-failures-v2.json', 'regressions'),
                      ('split-availability-diagnostics-v2.json', 'availability_diagnostics')]:
        suite = load_eval_suite(E / name)
        assert {c.id for c in suite.cases} == set(evidence[key])
        for case in suite.cases:
            assert case.id in seen
            assert (E / case.source['evidence']).is_file()
            assert not case.fixture or (case.fixture_root / case.fixture).is_dir()
    results = read(R / 'coverage-results.json')
    scores = {s: {'cases': r['cases'], 'passed': r['passed']} for s, r in results.items()}
    assert sum(v['cases'] for v in scores.values()) == 96
    assert sum(v['passed'] for v in scores.values()) == 89
    for path in (ROOT / 'scripts').glob('*.py'):
        ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    verification = {'cases': 96, 'scores': scores, 'categories': counts,
                    'source_files_unchanged': len(source) - len(source_drift),
                    'source_drift': source_drift,
                    'source_revision_verified': not source_drift,
                    'source_policy': 'Current hashes are a post-run snapshot; they do not establish the exact source imported by each prior worker.',
                    'behavioral_regressions': len(evidence['regressions']),
                    'availability_diagnostics': len(evidence['availability_diagnostics'])}
    (R / 'release-verification.json').write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (R / 'source-postrun-hashes.json').write_text(
        json.dumps(current_hashes, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(verification, ensure_ascii=False))


if __name__ == '__main__':
    main()
