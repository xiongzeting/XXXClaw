import json
from pathlib import Path
from collections import Counter,defaultdict
import hashlib

ROOT=Path('D:/MIniClaw/.aster/evals/efficiency-revision-v2')
IDS=['boundary_v1_config_migration_cli_contract','hard_v1_tiered_invoice','hard_v1_redaction_priority']
def label(m):
    s=str(m.get('content') or '')
    if m.get('role')=='system' and len(s)>6000:return 'system'
    if '<retrieved_memory>' in s:return 'retrieved_memory'
    if 'task_checkpoint' in s or '<task_progress' in s or 'Task checkpoint' in s:return 'task_checkpoint'
    if m.get('role')=='system' and ('checkpoint' in s.lower() or 'summary' in s.lower()) and len(s)<6000:return 'summary/system'
    if m.get('role')=='system':return 'system'
    return m.get('role','?')+':'+s[:75].replace('\n',' ')
def first(a,b):
    for i,(x,y) in enumerate(zip(a,b)):
        if x!=y:return i
    return min(len(a),len(b))
def dump(p,obj):p.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
allrows={}
for cid in IDS:
    file=ROOT/'run/cases'/cid/'attempt-001/workspace/.aster/eval-sessions/shared/trace.jsonl'
    events=[json.loads(x) for x in file.read_text(encoding='utf-8').splitlines()]
    req=[e for e in events if e['type']=='model.request' and e['data'].get('purpose')=='agent' and e['data'].get('status')=='success']
    rows=[];prev=None
    for n,e in enumerate(req,1):
        d=e['data'];ctx=d['context'];msgs=ctx['messages'];u=d['usage']
        row={'request':n,'run_id':e['run_id'],'input':u['input_tokens'],'cached':u['cached_tokens'],
            'messages':len(msgs),'head':[{'index':i,'label':label(m),'chars':len(str(m.get('content') or ''))} for i,m in enumerate(msgs[:5])],
            'projection':ctx.get('metadata',{}).get('context_projection',{}),
            'memory_chars':sum(len(m.get('content') or '') for m in msgs if label(m)=='retrieved_memory')}
        if prev:
            old=prev['data']['context'];idx=first(old['messages'],msgs)
            row.update({'same_system':old['messages'][0]==msgs[0], 'same_tools':old['tools']==ctx['tools'],
                'new_run':prev['run_id']!=e['run_id'],'first_changed_message':idx,
                'old_label':label(old['messages'][idx]) if idx<len(old['messages']) else 'END',
                'new_label':label(msgs[idx]) if idx<len(msgs) else 'END',
                'append_only':idx==len(old['messages']),
                'same_prefix_chars':sum(len(json.dumps(x,ensure_ascii=False)) for x in msgs[:idx])})
            if idx<len(old['messages']) and idx<len(msgs):
                a=str(old['messages'][idx].get('content') or '');b=str(msgs[idx].get('content') or '')
                cut=first(a,b);row['changed_content_char']=cut
                row['old_change']=a[max(0,cut-70):cut+150];row['new_change']=b[max(0,cut-70):cut+150]
        rows.append(row);prev=e
    groups=defaultdict(lambda:[0,0,0])
    for r in rows[1:]:
        key='append' if r['append_only'] else r['old_label']+' -> '+r['new_label']
        groups[key][0]+=1;groups[key][1]+=r['input'];groups[key][2]+=r['cached']
    print(cid,'requests',len(rows),'system changes',sum(not r['same_system'] for r in rows[1:]),'tool changes',sum(not r['same_tools'] for r in rows[1:]))
    print(json.dumps(dict(groups),ensure_ascii=False))
    print('head',rows[0]['head'])
    allrows[cid]=rows
dump(ROOT/'cache-prefix-analysis.json',allrows)
