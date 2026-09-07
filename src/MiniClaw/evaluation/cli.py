from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from MiniClaw.llm.env_file import merged_environment, read_env_file

from .models import load_eval_suite
from .runner import run_eval_suite


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="miniclaw-eval",
        description="Run fixed MiniClaw cases through the real Agent, tools, runtime, and graders.",
    )
    parser.add_argument("suite", help="Version 1 Eval suite JSON")
    parser.add_argument("--out", help="New output directory")
    parser.add_argument("--env-file", help="Dotenv file; defaults to ./.env")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument(
        "--repeat",
        type=int,
        help="Run every selected case N times (1-20) to measure pass^k and flakiness",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Run up to N cases concurrently (1-16)",
    )
    parser.add_argument("--baseline", help="Previous summary.json for regression checks")
    parser.add_argument("--save-baseline", help="Write this run's summary to a baseline path")
    return parser


async def _run(args: argparse.Namespace) -> None:
    suite = load_eval_suite(args.suite)
    env_path = Path(args.env_file) if args.env_file else Path.cwd() / ".env"
    file_values = read_env_file(env_path) if env_path.is_file() else {}
    environment = merged_environment(file_values)
    output = Path(args.out) if args.out else (
        Path.cwd()
        / ".aster"
        / "evals"
        / f"{suite.name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    report = await run_eval_suite(
        suite,
        output_directory=output,
        environment=environment,
        selected_cases=set(args.cases) if args.cases else None,
        provider=args.provider,
        model_id=args.model,
        baseline_path=args.baseline,
        repeat=args.repeat,
        jobs=args.jobs,
    )
    if args.save_baseline:
        baseline = Path(args.save_baseline).resolve()
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text(
            json.dumps(report["summary"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    summary = report["summary"]
    print(
        f"Eval: {summary['passed']}/{summary['cases']} passed "
        f"({summary['pass_rate']:.1%}), tokens={summary['metrics'].get('total_tokens', 0)}, "
        f"cost=${summary['metrics'].get('cost_usd', 0.0):.6f} -> {output.resolve()}"
    )
    if summary["regression_alerts"]:
        raise SystemExit(2)
    if summary["failed"] or (
        summary.get("coverage_enforced", True)
        and not summary.get("coverage_complete", True)
    ):
        raise SystemExit(1)


def main() -> None:
    asyncio.run(_run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
