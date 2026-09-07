"""Retire the first two rounds' retained partition without changing frozen evidence."""
import copy
import hashlib
import json
from collections import Counter,defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];E=ROOT/'evals'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def main():
    policy_path=E/'current-round-partitions.json'
    if not policy_path.exists():
        rounds={}
        for name,campaign in [('round1','hard-campaign-v1'),('round2','boundary-campaign-v1')]:
            original=read(ROOT/f'.aster/evals/{campaign}/snapshot/evals/{campaign}.json')
            mapping={c['id']:c['source']['split'] for c in original['cases']}
            groups=defaultdict(list)
            for c in original['cases']:
                if c['source']['split']=='retained':groups[c['category']].append(c['id'])
            index=0
            for category in sorted(groups):
                for cid in sorted(groups[category],key=lambda k:hashlib.sha256(k.encode()).hexdigest()):
                    mapping[cid]='development' if index%2==0 else 'test';index+=1
            rounds[name]={'assignments':mapping,'counts':dict(Counter(mapping.values())),
                'original_splits':{c['id']:c['source']['split'] for c in original['cases']}}
        dump(policy_path,{'version':1,'policy':'category-stratified stable SHA-256 order, alternating development/test; no score-based assignment',
            'holdout_status':'deferred; create new families only when explicitly requested for MiniClaw/Codex comparison',
            'exposure':'Both rounds have been executed/reviewed. Internal test is not an unseen holdout.', 'rounds':rounds})
    from eval_partition_policy import reclassify,metadata
    # Canonical editable suites; frozen .aster snapshots and raw result archives stay intact.
    for round_name,campaign in [('round1','hard-campaign-v1'),('round2','boundary-campaign-v1')]:
        suite=read(E/f'{campaign}.json')
        suite['cases']=[reclassify(c,round_name) for c in suite['cases']]
        suite['partition_policy']='current-round-partitions-v1'
        dump(E/f'{campaign}.json',suite)
        active_name='main-challenge-v5' if round_name=='round1' else campaign
        active=read(E/f'{active_name}.json')
        if 'cases' not in active:
            active={'version':active['version'],'name':active['name'],
                    'cases':[c for f in active['includes'] for c in read(E/f)['cases']]}
        active['cases']=[reclassify(c,round_name) for c in active['cases']]
        dump(E/f'{active_name}.json',active)
        for split in ('development','test','regression'):
            items=[c for c in active['cases'] if c['source']['split']==split]
            if items:dump(E/f'{round_name}-{split}-current.json',{'version':1,'name':f'{round_name}-{split}-current','cases':items})
        portfolio=E/('active-eval-portfolio-v5.json' if round_name=='round1' else 'active-eval-portfolio-v6.json')
        config=read(portfolio)
        config['main_combined']=f'{active_name}.json'
        config['main']={s:f'{round_name}-{s}-current.json' for s in ('development','test')}
        config['holdout_status']='deferred; no active retained suite'
        config['partition_policy']='current-round-partitions.json'
        config['current_counts']=metadata(round_name)['counts']
        config['validation']='Partition migration only; original model scores unchanged; all cases exposed.'
        if round_name=='round2':config['next_evaluation']='next-quality-limits-v1.json'
        dump(portfolio,config)
    # Existing editable subsets must agree with the policy even when invoked directly.
    for p in E.glob('*.json'):
        if p.name in {'current-round-partitions.json','next-quality-limits-v1.json'}:continue
        value=read(p)
        if not isinstance(value,dict) or not isinstance(value.get('cases'),list) or not value['cases']:continue
        if not all(isinstance(c,dict) and 'id' in c and 'source' in c for c in value['cases']):continue
        changed=False;rows=[]
        for c in value['cases']:
            group=next((g for g in ('round2','round1') if c['id'] in metadata(g)['assignments'] and metadata(g)['original_splits'][c['id']]=='retained'),None)
            if group and c['source'].get('split')=='retained':c=reclassify(c,group);changed=True
            rows.append(c)
        if changed:
            value['cases']=rows;value['partition_policy']='current-round-partitions-v1'
            dump(p,value)
    # Redistribute versioned split siblings together; retained filenames are retired.
    for retained in list(E.glob('retained-challenge-v*.json'))+[E/'boundary-retained-v1.json']:
        if not retained.exists():continue
        files={s:E/retained.name.replace('retained',s) for s in ('development','retained','test')}
        if not all(p.exists() for p in files.values()):continue
        suites={s:read(p) for s,p in files.items()};allcases={c['id']:c for v in suites.values() for c in v['cases']}
        for split,p in files.items():
            suites[split]['cases']=[c for c in allcases.values() if c['source']['split']==split]
            if split=='retained':suites[split]['status']='retired; cases moved to development/test; no new holdout'
            dump(p,suites[split])
    print({g:metadata(g)['counts'] for g in ('round1','round2')})
if __name__=='__main__':main()
