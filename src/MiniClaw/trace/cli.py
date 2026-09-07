from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from MiniClaw.llm.env_file import merged_environment, read_env_file

from .analysis import create_eval_case, generate_dashboard, migrate_legacy_traces
from .replay import replay_trace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="miniclaw-trace", description="MiniClaw Trace and Eval data loop")
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrate = subparsers.add_parser("migrate", help="Migrate legacy tool-calls/model-requests JSONL")
    migrate.add_argument("root")

    eval_case = subparsers.add_parser("eval-case", help="Create an Eval Case from a real failed run")
    eval_case.add_argument("session_dir")
    eval_case.add_argument("--run-id")
    eval_case.add_argument("--out", required=True)

    replay = subparsers.add_parser("replay", help="Replay recorded model boundaries without executing tools")
    replay.add_argument("session_dir")
    replay.add_argument("--run-id")
    replay.add_argument("--env-file")
    replay.add_argument("--provider")
    replay.add_argument("--model")
    replay.add_argument("--base-url")
    replay.add_argument("--prompt")
    replay.add_argument("--tools")
    replay.add_argument("--case")
    replay.add_argument("--results")
    replay.add_argument("--out", required=True)

    report = subparsers.add_parser("report", help="Build quality/cost dashboard and regression alerts")
    report.add_argument("root")
    report.add_argument("--out", required=True)
    report.add_argument("--baseline")
    return parser


async def _run(args: argparse.Namespace) -> None:
    if args.command == "migrate":
        print(json.dumps(migrate_legacy_traces(args.root), ensure_ascii=False))
        return
    if args.command == "eval-case":
        case = create_eval_case(args.session_dir, args.out, args.run_id)
        print(f"Eval Case: {case['id']} -> {Path(args.out).resolve()}")
        return
    if args.command == "report":
        summary = generate_dashboard(args.root, args.out, args.baseline)
        print(
            f"Dashboard: {summary['runs']} runs, {summary['successful_runs']} successful, "
            f"{len(summary['regression_alerts'])} alerts -> {Path(args.out).resolve()}"
        )
        return
    file_values = read_env_file(args.env_file) if args.env_file else {}
    environment = merged_environment(file_values)
    report = await replay_trace(
        args.session_dir,
        environment=environment,
        output_path=args.out,
        run_id=args.run_id,
        provider=args.provider,
        model_id=args.model,
        base_url=args.base_url,
        prompt_path=args.prompt,
        tool_config_path=args.tools,
        eval_case_path=args.case,
        results_path=args.results,
    )
    print(
        f"Replay: {report['summary']['requests']} requests, {report['summary']['errors']} errors, "
        f"${report['summary']['total_cost_usd']:.6f} -> {Path(args.out).resolve()}"
    )


def main() -> None:
    asyncio.run(_run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
