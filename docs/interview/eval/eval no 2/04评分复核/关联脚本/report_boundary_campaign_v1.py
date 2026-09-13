"""Read and export results only after the whole frozen batch has finished."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'.aster/evals/boundary-campaign-v1'


def main():
    assert (BASE/'completion.json').exists(), 'Do not inspect unfinished batch scores'
    report=json.loads((BASE/'run/report.json').read_text(encoding='utf-8'))
    suite=json.loads((ROOT/'evals/boundary-campaign-v1.json').read_text(encoding='utf-8'))
    specs={c['id']:c for c in suite['cases']}
    rows=[]
    failed=[]
    for case in report['cases']:
        attempt=case['attempts'][0]
        checks=[c for c in attempt['checks'] if c.get('required',True) and not c['passed']]
        metric=attempt['metrics']
        row={'id':case['id'],'category':case['category'],'split':specs[case['id']]['source']['split'],
             'passed':case['passed'],'error':attempt.get('error'),'failed_checks':checks,
             'metrics':{k:metric.get(k) for k in ['successful_runs','paused_runs','network_model_errors','non_network_model_errors',
                 'network_recovered_requests','network_recovered_runs','unrecovered_runs','total_tokens','tool_calls','compactions']},
             'phases':{k:{'final_text':v.get('final_text'),'errors':v.get('errors'),'trace_path':v.get('trace_path')}
                       for k,v in attempt['phases'].items()},'workspace':attempt['workspace']}
        rows.append(row)
        if not case['passed']:
            original=specs[case['id']]
            original['source'].setdefault('original_split',original['source']['split'])
            original['source']['split']='regression'
            original['source']['usage']='exposed development regression after batch review'
            original['source']['evidence']=f'evidence/boundary-v1/{case["id"]}.json'
            failed.append(original)
            evidence=ROOT/'evals/evidence/boundary-v1'/f'{case["id"]}.json'
            evidence.parent.mkdir(parents=True,exist_ok=True)
            evidence.write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (BASE/'review-input.json').write_text(json.dumps({'summary':report['summary'],'cases':rows},ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'evals/boundary-observed-failures-v1.json').write_text(json.dumps({
        'version':1,'name':'boundary-observed-failures-v1','cases':failed,
        'note':'Observed failures, not all confirmed Agent defects. Review oracle/network/budget evidence separately.'
    },ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for row in rows:
        print(row['id'],row['passed'],row['metrics'],
              [(c['dimension'],c['type'],c['detail'][:500]) for c in row['failed_checks']])


if __name__=='__main__': main()
