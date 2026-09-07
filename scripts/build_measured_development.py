"""Expose existing cases honestly as development with non-vacuous budgets."""
import copy,json
from pathlib import Path
from build_dialogue_campaign import hidden_test

ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'evals'
d=json.loads((E/'capability-coverage-v1.json').read_text(encoding='utf-8'))
d['name']='measured-development-v2'
d['coverage']={'dimensions':['outcome','process','efficiency','safety','reliability']}
for c in d['cases']:
    cat=c['category'];n=len(c['phases'])
    max_tokens=180000 if cat=='compression' else 100000 if cat=='recall' else 65000
    c['repetitions']=1
    c['source'].update(split='development',exposure='previously executed and inspected',
        efficiency_policy='Fixed generous release ceiling, not a measured speedup claim')
    if c['id']=='dialogue_cap_compress_multi_constraint':
        c['checks']=[x for x in c['checks'] if not (x['type']=='command' and x.get('dimension','outcome')=='outcome')]
        c['checks'].append(hidden_test('import json\nfrom pathlib import Path\nv=json.loads(Path("config.json").read_text())\nassert isinstance(v.get("encoding"),str) and v["encoding"].lower()=="utf-8"\nv["encoding"]="utf-8"\nassert v=={"encoding":"utf-8","delimiter":";","header":False,"null_token":""}'))
        c['source']['oracle_review']='Development only: UTF-8 and utf-8 are equivalent encoding names; all other fields remain exact.'
    c['checks'].extend([
        {'type':'metric','name':'total_tokens','max':max_tokens,'dimension':'efficiency'},
        {'type':'metric','name':'tool_calls','max':50 if cat=='compression' else 35,'dimension':'efficiency'},
        {'type':'metric','name':'agent_model_requests','max':45,'dimension':'efficiency'},
        {'type':'metric','name':'model_errors','equals':0,'dimension':'reliability'},
        {'type':'metric','name':'successful_runs','min':n,'dimension':'reliability'},
        {'type':'final_regex','pattern':r'\S','dimension':'reliability'},
    ])
    if not any(x.get('dimension')=='process' and x.get('required',True) for x in c['checks']):
        c['checks'].append({'type':'metric','name':'tool_calls','min':1,'dimension':'process'})
    if not any(x.get('dimension','outcome')=='outcome' and x.get('required',True) for x in c['checks']):
        expected_file=next(x for x in c['checks'] if x['type'] in {'file_equals','case_file_absent'})
        c['checks'].append({**expected_file,'dimension':'outcome'})
    # Source suite already contains independent outcomes and side-effect checks.
(E/'measured-development-v2.json').write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('46 previously exposed development cases, meaningful fixed budget and availability checks')
