"""Export failures without hiding infrastructure errors or altering test data."""
import copy,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];E=ROOT/'evals';R=ROOT/'.aster/evals/three-split-v2'
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def dump(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
specs={}
for name in ['measured-development-v2.json','retained-release-v2.json','test-release-v2.json']:
    s=read(E/name)
    for c in s['cases']:
        specs[c['id']]={**c,'environment':{**s.get('environment',{}),**c.get('environment',{})}}
collected=read(R/'coverage-results.json');regressions=[];availability=[]
for split,group in collected.items():
    report=read(R/split/'report.json')
    raw={c['id']:c for c in report['cases']}
    for c in group['results']:
        if c['passed']:continue
        spec=copy.deepcopy(specs[c['id']]);original=raw[c['id']]
        infra=any(a['infrastructure_or_terminal_model_error'] for a in c['attempts'])
        spec['repetitions']=3
        spec['source'].update(original_split=split,track='availability-diagnostic' if infra else 'failure-regression',
            status='observed-unfixed',evidence=f'evidence/three-split-v2/{c["id"]}.json',
            exposure='Observed test failures are now development regression examples; do not claim unseen performance on reruns')
        (availability if infra else regressions).append(spec)
        observations=[]
        for a in original['attempts']:
            events=[];seen=set()
            for phase in a['phases'].values():
                p=Path(phase['trace_path'])
                if p in seen:continue
                seen.add(p)
                for line in p.read_text(encoding='utf-8').splitlines():
                    row=json.loads(line)
                    if row['type'] in {'tool.call','approval.requested','approval.decision','compaction.completed'} or (row['type']=='model.request' and row['data'].get('status')!='success'):
                        events.append(row)
            observations.append({'passed':a['passed'],'answers':{k:v['final_text'] for k,v in a['phases'].items()},
                                 'workspace_changes':a['workspace_changes'],'checks':a['checks'],'events':events})
        dump(E/'evidence/three-split-v2'/f'{c["id"]}.json',{'id':c['id'],'original_split':split,'infrastructure_affected':infra,
             'phases':spec['phases'],'required_checks':spec['checks'],'observations':observations,
             'reviewed_result':c,'original_report':report.get('case_result_origins',{}).get(c['id'],str((R/split/'report.json').relative_to(ROOT)))})
for name,cases in [('split-failures-v2',regressions),('split-availability-diagnostics-v2',availability)]:
    if cases:dump(E/(name+'.json'),{'version':1,'name':name,'cases':cases})
dump(R/'failure-export-summary.json',{'regressions':[c['id'] for c in regressions],'availability_diagnostics':[c['id'] for c in availability],
                                    'policy':'All cases already executed; availability diagnostics are not asserted to be deterministic Agent bugs.'})
print(f'{len(regressions)} behavioral regression cases; {len(availability)} availability diagnostic cases')
