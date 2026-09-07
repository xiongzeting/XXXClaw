"""Real Feishu router + real model, with local in-memory message transport.

No external Feishu messages are sent. Incoming authors, conversation keys, queues,
memory scopes and the agent are the actual product implementation.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import shutil
import time

from MiniClaw.evaluation.models import load_eval_suite
from MiniClaw.evaluation.runner import evaluate_check, _aggregate_metrics, _combine_metrics, _snapshot_workspace, _workspace_changes
from MiniClaw.llm.env_file import read_env_file, merged_environment
from MiniClaw.llm.config import load_llm_settings
from MiniClaw.llm.factory import create_model_client, model_profile_from_settings
from MiniClaw.coding_agent.runtime import load_runtime_settings
from MiniClaw.coding_agent.approval import load_approval_settings
from MiniClaw.platforms.feishu.config import FeishuSettings
from MiniClaw.platforms.feishu.models import FeishuInboundMessage, build_conversation
from MiniClaw.platforms.feishu.router import FeishuAssistantRouter
from MiniClaw.trace.store import read_trace_records


class LocalTransport:
    def __init__(self):
        self.parents={}
        self.messages={}
    async def reply(self,message_id,text,*,in_thread):
        result=f'local-{len(self.parents)}'
        self.parents[result]=message_id
        self.messages.setdefault(message_id,[]).append(text)
        return result
    async def update(self,message_id,text):
        parent=self.parents[message_id]
        self.messages.setdefault(parent,[]).append(text)


async def run_attempt(case,suite,base_env,root):
    root.mkdir(parents=True,exist_ok=True)
    workspace=root/'workspace'
    shutil.copytree((case.fixture_root/case.fixture).resolve(),workspace)
    env={**base_env,**suite.environment,**case.environment}
    settings=load_llm_settings(provider='primary',model_id='gpt-5.6-luna',environment=env)
    transport=LocalTransport()
    session_scope=case.source.get('session_scope','thread')
    router=FeishuAssistantRouter(workspace=workspace,model_client=create_model_client(settings),
        profile=model_profile_from_settings(settings),settings=FeishuSettings('LOCAL_ONLY','LOCAL_ONLY',session_scope=session_scope),
        transport=transport,runtime_settings=load_runtime_settings(env),
        approval_settings=load_approval_settings(workspace,env),trace_provider='primary')
    before=_snapshot_workspace(workspace)
    phases={}; traces={}; messages=[]; error=None
    started=time.perf_counter()
    try:
        async with asyncio.timeout(case.timeout_seconds):
            offsets={}
            for index,phase in enumerate(case.phases):
                actor=case.source['actors'][index]
                mid=f'{case.id}-{index}'
                message=FeishuInboundMessage(message_id=mid,chat_id='fixture-group',user_id=actor,
                    tenant_id='fixture-tenant',chat_type='group',message_type='text',text=phase.prompt,
                    root_id='fixture-thread',mentioned=True)
                key=build_conversation(message,session_scope).session_key
                await router.submit(message)
                await router._queues[key].queue.join()
                assistant=router._assistants[key]
                trace=workspace/'.aster'/'feishu'/'sessions'/key/'trace.jsonl'
                records=read_trace_records(trace)
                traces[phase.id]=records[offsets.get(key,0):]; offsets[key]=len(records)
                final=transport.messages[mid][-1]
                messages.append({'phase':phase.id,'inbound_actor':actor,'input':phase.prompt,'output':final,
                    'bound_memory_user_scope':assistant.memory.user_scope})
                phases[phase.id]={'id':phase.id,'final_text':final,'trace_path':str(trace),'errors':[]}
    except Exception as exc:
        error=f'{type(exc).__name__}: {exc}'
    finally:
        await router.close()
    metrics=_aggregate_metrics(traces)
    changes=_workspace_changes(before,_snapshot_workspace(workspace))
    checks=[evaluate_check(c,workspace,root,phases,traces,metrics=metrics,workspace_changes=changes) for c in case.checks]
    result={'id':case.id,'passed':error is None and all(c['passed'] for c in checks),
        'error':error,'phases':phases,'messages':messages,'checks':checks,'metrics':metrics,
        'workspace':str(workspace),'workspace_changes':changes,'duration_seconds':round(time.perf_counter()-started,3),
        'failure_reasons':([error] if error else [])+[f"{c['dimension']}:{c['type']}:{c['detail']}" for c in checks if not c['passed']]}
    (root/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


async def main(args):
    suite=load_eval_suite(args.suite)
    if args.cases:
        requested=set(args.cases)
        if requested-{c.id for c in suite.cases}:raise ValueError('Unknown case selection')
        suite=replace(suite,cases=tuple(c for c in suite.cases if c.id in requested))
    if any(c.source.get('input_adapter')!='feishu-router' for c in suite.cases):
        raise ValueError('Router runner requires feishu-router cases; use run_dialogue_portfolio.py for a mixed suite')
    out=Path(args.out).resolve()
    if out.exists() and any(out.iterdir()): raise FileExistsError(out)
    out.mkdir(parents=True,exist_ok=True)
    env=merged_environment(read_env_file(args.env_file))
    semaphore=asyncio.Semaphore(args.jobs)
    async def one(case):
        async with semaphore:
            attempts=[]
            for index in range(args.repeat or case.repetitions):
                attempts.append(await run_attempt(case,suite,env,out/'cases'/case.id/f'attempt-{index+1:03d}'))
            result={'id':case.id,'category':case.category,'passed':all(a['passed'] for a in attempts),
                'passed_attempts':sum(a['passed'] for a in attempts),'repetitions':len(attempts),'attempts':attempts,
                'metrics':_combine_metrics([a['metrics'] for a in attempts]),
                'failure_reasons':[r for a in attempts for r in a['failure_reasons']]}
            (out/'cases'/case.id/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            return result
    results=await asyncio.gather(*(one(c) for c in suite.cases))
    summary={'suite':suite.name,'cases':len(results),'passed':sum(r['passed'] for r in results),
        'failed':sum(not r['passed'] for r in results),'attempts':sum(r['repetitions'] for r in results),
        'metrics':_combine_metrics([r['metrics'] for r in results]),'jobs':args.jobs,
        'transport':'local fake Feishu transport; actual router and actual gpt-5.6-luna',
        'limitations':'No live Feishu API; legacy aggregate latency quantiles are not overall quantiles.'}
    (out/'report.json').write_text(json.dumps({'summary':summary,'cases':results},ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"Router Eval: {summary['passed']}/{summary['cases']}; tokens={summary['metrics']['total_tokens']}",flush=True)
    return int(summary['failed']>0)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('suite');p.add_argument('--out',required=True);p.add_argument('--env-file',default='.env')
    p.add_argument('--jobs',type=int,default=4);p.add_argument('--repeat',type=int)
    p.add_argument('--case',action='append',dest='cases')
    raise SystemExit(asyncio.run(main(p.parse_args())))
