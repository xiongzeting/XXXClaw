"""Inspect frozen requests for outcome attribution; no model calls or candidate execution."""
import collections
import json
import re
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(json.loads((ROOT/'.aster/evals/eval3-hardened-r1/active-run.json').read_text('utf-8'))['directory'])
J=json.loads(Path(__file__).with_name('assistant_judgments.json').read_text('utf-8'))
MODE=sys.argv[1] if len(sys.argv)>1 else 'overview'

def records(cid):
    rows=[]
    for p in (OUT/'run/cases'/cid/'attempt-001/workspace/.aster/eval-sessions').rglob('trace.jsonl'):
        rows += [json.loads(line) for line in p.read_text('utf-8').splitlines() if line.strip()]
    return sorted(rows,key=lambda x:x.get('timestamp',''))

def emit(value):print(json.dumps(value,ensure_ascii=False))

if MODE=='overview':
    for c in J['cases']:
        if c['passed']:continue
        cid=c['id'];rows=records(cid)
        result=json.loads((OUT/'run/cases'/cid/'result.json').read_text('utf-8'))
        events=collections.Counter(r['type'] for r in rows)
        requests=[r for r in rows if r['type']=='model.request']
        purposes=collections.Counter(r['data'].get('purpose') for r in requests)
        emit({'id':cid,'events':dict(events),'purposes':dict(purposes),
              'phases':[{'phase':k,'prompt':v['prompt'],'answer':v.get('final_text','')[:1600]} for k,v in result['phases'].items()]})
elif MODE=='presence':
    for c in J['cases']:
        if c['passed']:continue
        cid=c['id'];rows=records(cid)
        result=json.loads((OUT/'run/cases'/cid/'result.json').read_text('utf-8'))
        req=[r for r in rows if r['type']=='model.request' and r['data'].get('purpose')=='agent']
        last=req[-1];messages=last['data']['context']['messages']
        text='\n'.join(m.get('content') or '' for m in messages if m.get('role')=='user')
        emit({'id':cid,'last_request_event':last['event_id'],'user_prompts_present':[k for k,v in result['phases'].items() if v['prompt'] in text],
              'phase_count':len(result['phases']),'compaction_events':[r['type'] for r in rows if 'compact' in r['type'] or 'archive' in r['type']],
              'purposes':dict(collections.Counter(r['data'].get('purpose') for r in rows if r['type']=='model.request'))})
else:
    cid=MODE
    rows=records(cid)
    result=json.loads((OUT/'run/cases'/cid/'result.json').read_text('utf-8'))
    phases={rid:k for k,v in result['phases'].items() for rid in v.get('run_ids',[])}
    if cid=='memory_scope_conflict':
        req=[r for r in rows if r['type']=='model.request' and r['data'].get('purpose')=='agent']
        selected={next(r['event_id'] for r in req if phases.get(r.get('run_id'))=='turn3'),req[-1]['event_id']}
        for r in rows:
            if phases.get(r.get('run_id')) not in ('turn3','turn6'):continue
            if r['type']=='model.request':
                if r['event_id'] not in selected:continue
                d=r['data'];messages=d.get('context',{}).get('messages',[])
                emit({'event':r['event_id'],'phase':phases.get(r.get('run_id')),'purpose':d.get('purpose'),
                      'messages':[{'role':m.get('role'),'name':m.get('name'),'snippets':[m.get('content','')[max(0,x.start()-100):x.end()+220] for x in list(re.finditer('5432|7777|signed-audit >|signed-audit>|来源优先级',m.get('content','') or ''))[:4]]} for m in messages if any(t in (m.get('content') or '') for t in ('5432','7777','来源优先级','signed-audit>'))]})
            elif r['type']=='tool.call' and r['data'].get('tool_name')=='memory':
                emit({'event':r['event_id'],'phase':phases.get(r.get('run_id')),'memory_call':r['data'].get('arguments'),'status':r['data'].get('status')})
    else:
        req=[r for r in rows if r['type']=='model.request' and r['data'].get('purpose')=='agent']
        r=req[-1]
        emit({'event':r['event_id'],'phase':phases.get(r.get('run_id')),'last_request_messages':r['data']['context']['messages']})
