"""Collect observed failures for repeat confirmation, without editing frozen sets."""
import copy,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];E=ROOT/'evals';R=ROOT/'.aster/evals/three-split-v2'
sources={'development':'measured-development-v2.json','retained':'retained-release-v2.json','test':'test-release-v2.json'}
cases=[];manifest=[]
for split,file in sources.items():
    suite=json.loads((E/file).read_text(encoding='utf-8'))
    specs={c['id']:c for c in suite['cases']}
    report=R/split/'report.json'
    if not report.is_file():continue
    reviewed=R/split/'reviewed-results.json'
    review={r['id']:r for r in json.loads(reviewed.read_text(encoding='utf-8'))['results']} if reviewed.is_file() else {}
    for row in json.loads(report.read_text(encoding='utf-8'))['cases']:
        if row['id'] in review:
            ok=review[row['id']]['reviewed_passed']
        else:ok=row['passed']
        if ok:continue
        infra=any(a.get('error') or a['metrics'].get('model_errors',0) for a in row['attempts'])
        meta={'id':row['id'],'original_split':split,'infrastructure_affected':infra,
              'report':str(report.relative_to(ROOT)),'failure_reasons':row['failure_reasons']}
        manifest.append(meta)
        if infra:continue
        case=copy.deepcopy(specs[row['id']])
        case['environment']={**suite.get('environment',{}),**case.get('environment',{})}
        case['repetitions']=2
        case['source'].update(track='development-failure-confirmation',original_split=split,
            first_report=str(report.relative_to(ROOT)),exposure='Failure revealed; subsequent runs are regression, not unseen testing')
        cases.append(case)
if cases:
    (E/'split-failure-candidates-v2.json').write_text(json.dumps({'version':1,'name':'split-failure-confirmation-v2','cases':cases},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(R/'failure-candidate-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(f'{len(cases)} non-infrastructure candidate cases; {len(manifest)} total failures')
