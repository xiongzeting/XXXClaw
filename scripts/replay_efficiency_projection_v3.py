"""Read v2 request traces and estimate history projection only; no model calls."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
SNAPSHOT=ROOT/'.aster/evals/efficiency-revision-v3/snapshot'
sys.path.insert(0,str(SNAPSHOT/'src'))
from MiniClaw.coding_agent.memory.config import load_memory_config
from MiniClaw.coding_agent.memory.working import WorkingContext, _message_from_dict, estimate_context_tokens
from MiniClaw.llm.types import ModelProfile


def main():
    suite=json.loads((SNAPSHOT/'evals/boundary-efficiency-v3.json').read_text(encoding='utf-8'))
    cases={c['id']:c for c in suite['cases']}
    rows=[]
    for directory in sorted((ROOT/'.aster/evals/efficiency-revision-v2/run/cases').iterdir()):
        if not directory.is_dir():continue
        records={}
        for path in directory.rglob('trace.jsonl'):
            for line in path.read_text(encoding='utf-8').splitlines():
                e=json.loads(line);records[e['event_id']]=e
        errors={e['data']['call_id'] for e in records.values() if e['type']=='tool.call'
                and e['data'].get('status')!='success' and 'call_id' in e['data']}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);profile=ModelProfile('gpt-5.6-luna')
            env={**suite.get('environment',{}),**cases[directory.name].get('environment',{})}
            context=WorkingContext(path=root/'session.jsonl',workspace=root,session_id=directory.name,
                config=load_memory_config(profile,env),model_client=None,profile=profile)
            context._failed_call_ids.update(errors)
            totals={'requests':0,'shrunk_requests':0,'before_estimated_history_tokens':0,
                    'after_estimated_history_tokens':0,'summed_archived_pairs':0}
            for e in records.values():
                if e['type']!='model.request' or e['data'].get('purpose')!='agent':continue
                messages=[_message_from_dict(m) for m in e['data']['context']['messages']]
                projected=context.transform_request_context(messages,persist=False)
                before=estimate_context_tokens(messages);after=estimate_context_tokens(projected)
                totals['requests']+=1;totals['shrunk_requests']+=after<before
                totals['before_estimated_history_tokens']+=before;totals['after_estimated_history_tokens']+=after
                totals['summed_archived_pairs']+=context.last_projection.get('closed_tool_pairs_archived',0)
            assert not context.artifacts.root.exists()
        totals['estimated_reduction_pct']=round(100*(1-totals['after_estimated_history_tokens']/totals['before_estimated_history_tokens']),2)
        rows.append({'case':directory.name,**totals})
    out=ROOT/'.aster/evals/efficiency-revision-v3/projection-replay.json'
    out.write_text(json.dumps({'method':'Frozen v3 projection on v2 request snapshots; no model calls, artifact I/O, memory replacement or behavioral replay. Local char/4 estimates, not provider billing. Failed calls protected using all known trace failures.',
                              'cases':rows},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for row in rows: print(json.dumps(row,ensure_ascii=False))


if __name__=='__main__':main()
