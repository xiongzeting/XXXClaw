"""Add four program scores to a completed frozen collection, without model calls."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from MiniClaw.evaluation.models import load_eval_suite
from MiniClaw.evaluation.submissions import score_program_dimensions, PROGRAM_DIMENSIONS


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    batch = ROOT/'.aster/evals/boundary-full-v10-jobs20'
    assert (batch/'completion.json').is_file(), 'Wait for all tasks; never score a live workspace'
    freeze = json.loads((batch/'freeze.json').read_text(encoding='utf-8'))
    for group, key in [('src', 'source_hashes'), ('evals', 'eval_hashes')]:
        for rel, expected in freeze[key].items():
            assert digest(batch/'snapshot'/group/rel) == expected, rel
    suite = load_eval_suite(batch/'snapshot/evals/boundary-submissions-v10.json')
    output = batch/'run/program-scores'
    output.mkdir(exist_ok=True)
    results = []
    for case in suite.cases:
        source = batch/'run/cases'/case.id/'result.json'
        raw = json.loads(source.read_text(encoding='utf-8'))
        workspace = Path(raw['workspace'])
        for rel, expected in raw['artifact_sha256'].items():
            assert digest(workspace/rel) == expected, (case.id, rel)
        # Frozen suite uses only read-only trace/metric/path assertions here.
        assert all(c.type in {'metric','trace_event','workspace_diff','tool_write_paths','final_regex'}
                   for c in case.checks if c.dimension in PROGRAM_DIMENSIONS)
        score = score_program_dimensions(case, raw, source.parent/'attempt-001')
        result = {'id':case.id, 'raw_result_sha256':digest(source), **score}
        (output/(case.id+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        results.append(result)
    summary = {'cases':len(results), 'outcome_status':'pending', 'passed':None,
               'dimensions':{d:{'passed':sum(r['dimensions'][d]['score']==1 for r in results),
                                'scored':sum(r['dimensions'][d]['score'] is not None for r in results)}
                             for d in PROGRAM_DIMENSIONS},
               'source_report_sha256':digest(batch/'run/report.json'),
               'note':'Additive program regrade; original report and frozen code unchanged; no LLM calls.'}
    report = {'summary':summary, 'cases':results}
    (batch/'run/program-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
