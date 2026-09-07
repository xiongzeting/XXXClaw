"""Run suite raw user text through the real Feishu extraction boundary.

No Feishu service is contacted or message sent. This tests input extraction + the
real CodingAssistant, not the full transport/router integration. Non-adapter
cases pass through unchanged, allowing one combined release suite.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path

from MiniClaw.evaluation.models import load_eval_suite
from MiniClaw.evaluation.runner import run_eval_suite
from MiniClaw.llm.env_file import read_env_file, merged_environment
from MiniClaw.platforms.feishu.models import extract_message_text


async def main(args):
    suite=load_eval_suite(args.suite)
    if args.cases:
        requested=set(args.cases)
        if requested-{c.id for c in suite.cases}:raise ValueError('Unknown case selection')
        suite=replace(suite,cases=tuple(c for c in suite.cases if c.id in requested))
    cases=[]
    transformations=[]
    for case in suite.cases:
        adapter=case.source.get("input_adapter","")
        if adapter=='feishu-router':
            raise ValueError('Use run_dialogue_portfolio.py for suites containing real router cases')
        phases=[]
        for phase in case.phases:
            raw=phase.prompt
            if adapter == "feishu-text":
                delivered=extract_message_text("text",json.dumps({"text":raw},ensure_ascii=False),[])
            elif adapter == "feishu-post":
                payload={"zh_cn":{"title":"","content":[[{"tag":"text","text":line}] for line in raw.split("\n")]}}
                delivered=extract_message_text("post",json.dumps(payload,ensure_ascii=False),[])
            else:
                delivered=raw
            phases.append(replace(phase,prompt=delivered))
            if adapter:
                transformations.append({"case_id":case.id,"phase":phase.id,"adapter":adapter,
                    "raw":raw,"delivered":delivered,"changed":raw!=delivered,
                    "raw_sha256":hashlib.sha256(raw.encode()).hexdigest(),
                    "delivered_sha256":hashlib.sha256(delivered.encode()).hexdigest()})
        cases.append(replace(case,phases=tuple(phases)))
    environment=merged_environment(read_env_file(args.env_file))
    report=await run_eval_suite(replace(suite,cases=tuple(cases)),output_directory=args.out,
        environment=environment,provider="primary",model_id="gpt-5.6-luna",
        repeat=args.repeat,jobs=args.jobs)
    Path(args.out,"input-transformations.json").write_text(json.dumps(transformations,ensure_ascii=False,indent=2),encoding="utf-8")
    s=report['summary']
    print(f"Adapter-aware Eval: {s['passed']}/{s['cases']}; tokens={s['metrics']['total_tokens']}; cost={s['metrics']['cost_usd']}",flush=True)
    return int(s['failed']>0)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite')
    parser.add_argument('--out',required=True)
    parser.add_argument('--env-file',default='.env')
    parser.add_argument('--jobs',type=int,default=6)
    parser.add_argument('--repeat',type=int)
    parser.add_argument('--case',action='append',dest='cases')
    raise SystemExit(asyncio.run(main(parser.parse_args())))
