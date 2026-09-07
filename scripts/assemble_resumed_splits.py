"""Assemble full coverage after resumption; retain first-attempt evidence."""
import copy,json
from pathlib import Path
from MiniClaw.evaluation.models import load_eval_suite
from MiniClaw.evaluation.runner import _summarize

ROOT=Path(__file__).resolve().parents[1];R=ROOT/'.aster/evals/three-split-v2';E=ROOT/'evals'
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
recovery=R/'recovery-2'
release_manifest=read(E/'reviewed-split-release-manifest.json')
audit=[]
for split,file in [('retained','retained-release-v2.json'),('test','test-release-v2.json')]:
    suite=load_eval_suite(E/file);rows=[];origins={}
    for case in suite.cases:
        first=R/split/'cases'/case.id/'result.json'
        retry=recovery/'cases'/case.id/'result.json'
        replacement=R/'supplement'/'cases'/case.id/'result.json'
        if replacement.is_file():retry=replacement
        if retry.is_file():p=retry
        elif first.is_file():p=first
        else:raise RuntimeError('Case has not completed: '+case.id)
        row=read(p)
        rows.append(row)
        origins[case.id]=str(p.relative_to(ROOT))
        audit.append({'id':case.id,'split':split,'selected_result':str(p.relative_to(ROOT)),
            'first_result':str(first.relative_to(ROOT)) if first.is_file() else None,
            'first_passed':read(first)['passed'] if first.is_file() else None,
            'selected_passed':row['passed'],'resumed':p==retry})
    summary=_summarize(suite,rows,elapsed_seconds=sum(c['duration_seconds'] for c in rows))
    # Summed per-case seconds is explicitly not batch wall-clock time.
    summary['elapsed_seconds_kind']='sum_of_selected_case_durations_not_parallel_wall_time'
    report={'summary':summary,'cases':rows,'case_result_origins':origins,
        'assembly_policy':'Latest completed attempt after resource-limited resumption; not first-pass accuracy. Raw first results preserved.',
        'excluded_invalid_ids':list(release_manifest['replacements'][split])}
    (R/split/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (R/split/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(R/'resumption-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Assembled 25 retained and 25 test cases; raw first-attempt failures retained')
