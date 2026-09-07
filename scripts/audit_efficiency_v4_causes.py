"""Read-only audit of frozen implementations and completed evaluation traces."""
from pathlib import Path
import json
import difflib
import sys
import tempfile
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / '.aster/evals'
OUT = BASE / 'efficiency-revision-v4/cause-audit'
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(BASE/'efficiency-revision-v4/snapshot/src'))
from MiniClaw.coding_agent.assistant.verification import decode_verification
from MiniClaw.coding_agent.assistant.progress import TaskProgress
from MiniClaw.coding_agent.tools.base import ToolResult
from MiniClaw.llm.types import ToolInvocation

def read(p):
    return json.loads(p.read_text(encoding='utf-8'))

for rel in ['coding_agent/assistant/progress.py', 'coding_agent/assistant/acceptance.py',
            'coding_agent/assistant/coding.py', 'coding_agent/tools/bash.py',
            'coding_agent/memory/history_budget.py', 'agent/loop.py']:
    texts = {}
    for v in ['v2', 'v3', 'v4']:
        p = BASE / f'efficiency-revision-{v}/snapshot/src/MiniClaw' / rel
        texts[v] = p.read_text(encoding='utf-8').splitlines(keepends=True) if p.exists() else []
    for before, after in [('v2', 'v3'), ('v3', 'v4')]:
        diff = ''.join(difflib.unified_diff(texts[before], texts[after], fromfile=before+'/'+rel, tofile=after+'/'+rel))
        (OUT / (rel.replace('/', '_') + f'.{before}-{after}.diff')).write_text(diff, encoding='utf-8')

results = []
for c in read(BASE/'efficiency-revision-v4/run/report.json')['cases']:
    path = BASE / 'efficiency-revision-v4/run/cases' / c['id'] / 'attempt-001/workspace/.aster/eval-sessions/shared'
    events = [json.loads(line) for line in (path/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
    errors = []; checkpoints=[]; last_tokens=0; tools=Counter(); prefixes=Counter(); layouts=Counter(); history_details=[]; previous=None; diffs=Counter(); diff_examples=[]; metadata_samples=[]
    for n,e in enumerate(events):
        d=e['data']
        if e['type']=='model.request':
            last_tokens += d.get('usage',{}).get('total_tokens',0)
            prefixes[d.get('context',{}).get('metadata',{}).get('request_prefix',{}).get('first_changed_component','none')]+=1
            if d.get('context',{}).get('metadata',{}).get('request_prefix',{}).get('first_changed_component')=='history':
                history_details.append(d.get('context',{}))
                current=d.get('context',{}).get('messages',[])
                if previous and previous[0]==e['run_id']:
                    old=previous[1]
                    for i,(left,right) in enumerate(zip(old,current)):
                        if left!=right:
                            same_identity=left.get('role')==right.get('role') and left.get('tool_call_id')==right.get('tool_call_id') and [x.get('call_id') for x in left.get('tool_calls',[])]==[x.get('call_id') for x in right.get('tool_calls',[])]
                            label=right.get('role','unknown')+(' same_identity' if same_identity else ' replaced')
                            if same_identity and left.get('tool_calls')!=right.get('tool_calls'):label+=' arguments_changed'
                            diffs[label]+=1
                            if len(diff_examples)<3:
                                diff_examples.append({'line':n+1,'index':i,'old':left,'new':right})
                            break
                if len(metadata_samples)<2: metadata_samples.append(d['context'].get('metadata'))
            if d.get('purpose')=='agent':previous=(e['run_id'],d.get('context',{}).get('messages',[]))
        if e['type']!='tool.call':
            continue
        tools[d.get('tool_name')]+=1
        if d.get('tool_name')=='task_checkpoint':
            checkpoints.append({'line':n+1,'run_id':e['run_id'],'data':d})
        if d.get('details',{}).get('task_verification',{}).get('error'):
            errors.append({'line':n+1, 'run_id':e['run_id'], 'tokens_so_far':last_tokens, 'data':d})
            if 'missing comparison IDs' in d['details']['task_verification']['error']:
                payload, _ = decode_verification(ToolResult(d.get('result') or '', details=d['details']))
                if payload.get('comparisons'):
                    layouts['root_comparisons']+=1
                elif any(isinstance(x.get('evidence'),dict) and 'comparisons' in x['evidence'] for x in payload['task_checks']):
                    layouts['evidence_nested_comparisons']+=1
                elif any(x.get('comparisons') for x in payload['task_checks']):
                    layouts['per_item_partial_comparisons']+=1
                else:
                    layouts['no_comparisons_array']+=1
    result={'case':c['id'],'errors':errors,'checkpoints':checkpoints,'prefixes':dict(prefixes),'tools':dict(tools),'layouts':dict(layouts),'first_difference':dict(diffs),'diff_examples':diff_examples,'metadata_samples':metadata_samples}
    results.append(result)
    print(c['id'], 'checkpoints',len(checkpoints),'errors',len(errors),'prefixes',dict(prefixes))
    print('ERROR_LAYOUTS',dict(layouts))
    print('HISTORY_FIRST_DIFF',dict(diffs))
    state=read(path/'task-progress.json') if (path/'task-progress.json').exists() else {}
    print('CRITERIA',[(x['criterion_id'],x['version']) for x in state.get('criteria',[])])
    if history_details:
        (OUT/(c['id']+'-history-context.json')).write_text(json.dumps(history_details,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'trace-evidence.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')

# Tiny deterministic probes: execute no model, shell or benchmark task.
probes={}
with tempfile.TemporaryDirectory(prefix='miniclaw-v4-protocol-audit-') as temporary:
    progress=TaskProgress(Path(temporary)/'session', Path(temporary))
    criteria=[{'criterion_id':'a','description':'A','version':1,'check_ids':['a-value']},
              {'criterion_id':'b','description':'B','version':1,'check_ids':['b-value']}]
    progress.checkpoint('audit',[],'verify',criteria)
    def observe(items):
        raw=json.dumps({'task_checks':items})
        progress.observe(ToolInvocation('probe','bash',{'task_verification':True}),
            ToolResult(raw,details={'stdout':raw,'exit_code':0,'workspace_changes':{'complete':True,'changed_paths':[]}}))
    def check(key):
        return {'criterion_id':key,'version':1,'passed':True,'evidence':{'actual':1,'expected':1},
                'comparisons':[{'check_id':key+'-value','actual':1,'expected':1}]}
    advertised=check('a');advertised.pop('comparisons')
    observe([advertised]);probes['advertised_shape_error']=progress.state.get('verification_error')
    observe([check('a'),check('b')]);probes['valid_records_verified']=sorted(progress.state['verified'])
    broken=check('b');broken['comparisons']=[]
    observe([check('a'),broken]);probes['valid_a_plus_malformed_b_verified']=sorted(progress.state['verified'])
    probes['batch_error']=progress.state.get('verification_error')
    progress.checkpoint('audit',[],'verify',criteria)
    false_a=check('a');false_a['comparisons'][0]['actual']=2
    observe([false_a]);probes['failed_check_durable']=json.loads(json.dumps(progress.state.get('failed_checks')))
    probes['failed_check_in_snapshot']='failed_checks' in progress.snapshot()
    probes['failed_compare_feedback']=progress.final_blocker()
    progress.checkpoint('audit',[],'verify',criteria,withdrawn=[])
    observe([check('a'),check('b')])
    replacement=[{**x,'description':'Entirely different requirement'} for x in criteria]
    progress.checkpoint('audit',[],'verify',replacement)
    probes['changed_description_same_version_still_verified']=sorted(progress.state['verified'])
    progress.checkpoint('audit',[],'deliver',[],withdrawn=['a','b'])
    probes['withdraw_without_user_event_blocker']=progress.final_blocker()
(OUT/'mechanism-probes.json').write_text(json.dumps(probes,ensure_ascii=False,indent=2),encoding='utf-8')
print('PROBES',json.dumps(probes,ensure_ascii=False))
