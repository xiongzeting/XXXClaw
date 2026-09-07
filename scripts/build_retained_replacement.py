"""Fresh replacement for an ambiguous validation item; original remains frozen."""
import json,hashlib
from pathlib import Path
from build_dialogue_campaign import add,CASES,edits_only,write_suite,EVALS

add('retained_delegation_expiry','recall',[
    '请长期记住 Aster 项目的代理审批安排：2026-09-01 到 2026-09-03（包含两天端点）由 Nolan 代理审批；永久联系人是 Mira，紧急电话尾号为 7329。',
    '确认 Aster 的代理审批没有续期，代理期结束后无人拥有代理审批资格。永久联系人和电话没变。请保留有效期，不要把临时授权存成永久授权。',
    '现在日期为 2026-09-05。Aster 此刻代理审批人、永久联系人和电话尾号是什么？仅输出 JSON：delegate（当前无人授权时必须为 JSON null）、contact（字符串）、phone_suffix（字符串）。'
], {'delegate':None,'contact':'Mira','phone_suffix':'7329'},checks=[edits_only(),
    {'type':'trace_event','event':'memory.retrieval','phase':'turn3','where_min':{'data.injected_count':1},'dimension':'process'},
    {'type':'metric','name':'total_tokens','max':65000,'dimension':'efficiency'},
    {'type':'metric','name':'tool_calls','max':30,'dimension':'efficiency'},
    {'type':'metric','name':'successful_runs','min':3,'dimension':'reliability'},
    {'type':'metric','name':'model_errors','equals':0,'dimension':'reliability'},
    {'type':'final_regex','pattern':r'\S','dimension':'reliability'}])
c=CASES[-1]
c['environment']={'MINICLAW_MEMORY_CONSOLIDATION_ENABLED':'true'}
c['source'].update(track='validation',split='retained',family='temporary-delegation-with-expiry',
    replaces='dialogue_recall_retracted_contact',reason='Original null versus empty string unspecified; original frozen case excluded, not rewritten.')
c['timeout_seconds']=720
write_suite('fresh-retained-replacement-v1',CASES)
p=EVALS/'fresh-retained-replacement-v1.json'
manifest={'suite':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
          'policy':'New validation replacement frozen before first run; original suite and report preserved.'}
freeze=EVALS/'fresh-retained-replacement-freeze.json'
if freeze.exists():assert json.loads(freeze.read_text(encoding='utf-8'))==manifest
else:freeze.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('One fresh, explicit validation replacement frozen')
