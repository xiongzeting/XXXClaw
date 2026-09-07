"""Freeze three honest splits; never relabel exposed data as unseen."""
import hashlib,json
from collections import Counter
from pathlib import Path
from MiniClaw.evaluation.models import load_eval_suite

ROOT=Path(__file__).resolve().parents[1];E=ROOT/'evals'
DIMENSIONS={'outcome','process','efficiency','safety','reliability'}
FILES={'development':'measured-development-v2.json','retained':'fresh-retained-v1.json','test':'fresh-test-v1.json'}

def main():
    missing=[f for f in FILES.values() if not (E/f).is_file()]
    if missing:raise RuntimeError('Fresh suites not generated yet: '+', '.join(missing))
    rows={};seen_ids=set();prompts={};families={}
    for split,filename in FILES.items():
        suite=load_eval_suite(E/filename)
        counts=Counter(c.category for c in suite.cases)
        assert set(counts)=={'compression','recall','tools','safety','completion'},(split,counts)
        assert min(counts.values())>=5,(split,counts)
        matrix={}
        for c in suite.cases:
            assert c.id not in seen_ids,c.id
            seen_ids.add(c.id)
            measured={x.dimension for x in c.checks if x.required}
            assert DIMENSIONS<=measured,(split,c.id,'missing',DIMENSIONS-measured)
            for check in c.checks:
                if check.dimension in {'efficiency','reliability'} and check.type=='metric':
                    assert check.options.get('min')!=0,(c.id,'vacuous metric')
            digest=hashlib.sha256('\n'.join(p.prompt for p in c.phases).encode()).hexdigest()
            assert digest not in prompts or prompts[digest]==split,(c.id,'duplicate prompt')
            prompts[digest]=split
            if split!='development':
                family=c.source.get('family')
                assert family,(c.id,'missing family')
                assert family not in families or families[family]==split,(c.id,'family crosses split')
                families[family]=split
            matrix[c.id]=sorted(measured)
        rows[split]={'suite':filename,'count':len(suite.cases),'categories':dict(counts),
                     'sha256':hashlib.sha256((E/filename).read_bytes()).hexdigest(),'required_dimensions':matrix,
                     'fixture_hashes':{str(p.relative_to(E)):hashlib.sha256(p.read_bytes()).hexdigest()
                       for c in suite.cases if c.fixture for p in sorted((c.fixture_root/c.fixture).rglob('*')) if p.is_file()}}
    manifest={'version':2,'splits':rows,'policy':[
        'Previously exposed cases are development only.',
        'Fresh retained cases are validation, never called unseen test after results guide development.',
        'Test suite, fixtures and oracles frozen before execution. First run reveals it.',
        'No threshold/oracle tuning using test results. Invalid tests reported separately.',
        'Synthetic local suites; family and prompt checks do not guarantee semantic independence.',
        'Efficiency budgets are fixed ceilings; availability includes successful turns and no terminal model errors.',
    ]}
    target=E/'three-split-freeze-v2.json'
    if target.exists():assert json.loads(target.read_text(encoding='utf-8'))==manifest,'Frozen artifacts changed'
    else:target.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print({k:v['count'] for k,v in rows.items()})

if __name__=='__main__':main()
