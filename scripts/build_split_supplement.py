"""Queue frozen replacements, two infrastructure retries, and format confirmation."""
import copy,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];E=ROOT/'evals'
def read(name):return json.loads((E/name).read_text(encoding='utf-8'))
cases=[]
for file in ['fresh-test-replacements-v1.json','precise-split-replacements-v1.json']:
    s=read(file)
    cases += [{**c,'environment':{**s.get('environment',{}),**c.get('environment',{})}} for c in s['cases']]
for file,ids in [('fresh-retained-v1.json',['dialogue_tool_csv_unicode_path','dialogue_safety_permission_revoke']),
                 ('fresh-test-v1.json',['fresh_test_json_patch'])]:
    s=read(file)
    for c in s['cases']:
        if c['id'] not in ids:continue
        new=copy.deepcopy(c)
        new['environment']={**s.get('environment',{}),**c.get('environment',{})}
        new['repetitions']=2 if c['id']=='dialogue_safety_permission_revoke' else 1
        new['source']['rerun_reason']='confirm output contract' if new['repetitions']==2 else 'retry terminal model error; do not hide first failure'
        cases.append(new)
payload={'version':1,'name':'frozen-split-supplement','cases':cases}
(E/'split-supplement-v2.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(f'{len(cases)} cases, {sum(c.get("repetitions",1) for c in cases)} attempts')
