"""Regrade exact recorded attempts with current oracles; keep raw reports intact."""
import argparse
import json
from pathlib import Path
from MiniClaw.evaluation.models import load_eval_suite
from MiniClaw.evaluation.runner import evaluate_check

ROOT=Path(__file__).resolve().parents[1]


def review(suite_file,run_dir):
    suite=load_eval_suite(suite_file)
    specs={c.id:c for c in suite.cases}
    output=[]
    for p in sorted(Path(run_dir).glob('cases/*/result.json')):
        raw=json.loads(p.read_text(encoding='utf-8'))
        spec=specs[raw['id']]
        attempts=[]
        for attempt in raw['attempts']:
            traces={}
            for phase,value in attempt['phases'].items():
                ids=set(value.get('run_ids') or [])
                rows=[json.loads(line) for line in Path(value['trace_path']).read_text(encoding='utf-8').splitlines()]
                traces[phase]=[r for r in rows if not ids or r.get('run_id') in ids]
            checks=[]
            for check in spec.checks:
                old=[x for x in attempt['checks'] if x['type']==check.type and x.get('options')==check.options]
                if check.type=='command' and len(old)==1:
                    result={**old[0], 'dimension':check.dimension, 'required':check.required}
                else:
                    result=evaluate_check(check,Path(attempt['workspace']),Path(attempt['workspace']).parent,
                        attempt['phases'],traces,metrics=attempt.get('metrics'),workspace_changes=attempt.get('workspace_changes'))
                checks.append(result)
            attempts.append({'passed':not attempt.get('error') and all(x['passed'] for x in checks if x.get('required',True)),
                             'checks':checks,'error':attempt.get('error')})
        row={'id':raw['id'],'category':raw['category'],'original_passed':raw['passed'],
             'reviewed_passed':all(a['passed'] for a in attempts),'attempts':attempts,'source_report':str(p.relative_to(ROOT))}
        output.append(row)
    result={'suite':str(suite_file),'cases':len(output),'passed':sum(r['reviewed_passed'] for r in output),'results':output}
    (Path(run_dir)/'reviewed-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Reviewed {result["passed"]}/{result["cases"]}')
    for r in output:
        if not r['reviewed_passed']:
            print(r['id'],[(x['type'],x['detail'][:220]) for a in r['attempts'] for x in a['checks'] if not x['passed']])
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('suite');p.add_argument('run')
    args=p.parse_args();review(Path(args.suite).resolve(),Path(args.run).resolve())
