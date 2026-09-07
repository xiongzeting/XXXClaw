"""Deprecated: redistributing exposed examples does not create a heldout set."""
raise RuntimeError('Invalid old partitioner. Use freeze_three_splits.py with fresh suites.')
import copy, json, hashlib
from pathlib import Path
from MiniClaw.evaluation.models import load_eval_suite

ROOT=Path(__file__).resolve().parents[1]; E=ROOT/'evals'
files=['dialogue-memory-development.json','dialogue-product-development.json','dialogue-challenge-development.json',
       'dialogue-long-history-development.json','dialogue-edges-development.json','dialogue-input-development.json',
       'dialogue-router-development.json','capability-all-development.json','capability-extension-development.json',
       'capability-followup-development.json','resilience.json']
cases=[]; seen=set()
for f in files:
    for c in json.loads((E/f).read_text(encoding='utf-8-sig')).get('cases',[]):
        if c['id'] in seen: continue
        seen.add(c['id']); cases.append(copy.deepcopy(c))
dimensions=['outcome','process','efficiency','safety','reliability']
def dims(c):return {x.get('dimension','outcome') for x in c.get('checks',[])}
for c in cases:
    # Every split records efficiency and reliability counters even when a
    # scenario's main oracle is outcome/process/safety.
    c.setdefault('checks',[]).extend([
        {'type':'metric','name':'total_tokens','min':0,'required':False,'dimension':'efficiency'},
        {'type':'metric','name':'model_errors','min':0,'required':False,'dimension':'reliability'},
    ])
splits={k:[] for k in ['development','retained','test']}
remaining=list(cases)
# Seed every split with cases covering every measured dimension.
for split in splits:
    for d in dimensions:
        idx=next((i for i,c in enumerate(remaining) if d in dims(c)),None)
        if idx is None: raise RuntimeError('No case for dimension '+d)
        splits[split].append(remaining.pop(idx))
# Deterministically distribute the rest; retain related variants in development,
# while test gets a stable hash holdout.
for c in remaining:
    bucket=int(hashlib.sha256(c['id'].encode()).hexdigest()[:8],16)%3
    splits[['development','retained','test'][bucket]].append(c)
env={'MINICLAW_SANDBOX':'docker:miniclaw-runtime:py313-bench','MINICLAW_WORKSPACE_MODE':'direct',
     'MINICLAW_APPROVAL_POLICY':'allow','MINICLAW_GOAL_JUDGE_ENABLED':'false'}
for name,items in splits.items():
    for c in items:
        c.setdefault('source',{})['split']=name
        c['source']['split_policy']='deterministic sha256 holdout; synthetic development data'
    payload={'version':1,'name':'miniclaw-'+name+'-v1','environment':env,
             'coverage':{'dimensions':dimensions,'capabilities':['compression_recovery','cross_session_recall','tool_execution','safety_boundary','end_to_end_delivery']},'cases':items}
    (E/f'{name}-v1.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    loaded=load_eval_suite(E/f'{name}-v1.json')
    assert len(loaded.cases)==len(items)
    got={d for c in items for d in dims(c)}
    assert set(dimensions)<=got,(name,got)
manifest={'version':1,'source_files':files,'counts':{k:len(v) for k,v in splits.items()},
          'dimensions':dimensions,'cases':{k:[c['id'] for c in v] for k,v in splits.items()},
          'policy':'Development may iterate; retained is stable release regression; test is deterministic holdout. None is public benchmark data.'}
(E/'eval-split-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(manifest['counts'])
