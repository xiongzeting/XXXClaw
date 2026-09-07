from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BENCH_ROOT = ROOT / "external" / "benchmarks" / "ContextWeave-main"
DEFAULT_SELECTION = BENCH_ROOT / "generated" / "miniclaw-coding-memory-6.json"
DEFAULT_RESULTS = ROOT / ".aster" / "benchmarks" / "results" / "contextweave"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def score_job(
    case: dict[str, Any],
    arm: str,
    *,
    run_root: Path,
    metrics_root: Path,
    base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: int,
    force: bool,
) -> dict[str, Any]:
    person = str(case["person"])
    subtask_id = str(case["subtask_id"])
    memory_system = "miniclaw-four-layer" if arm == "retrieval" else "miniclaw-concat"
    job_metrics = metrics_root / person / arm
    log_dir = metrics_root / "_launcher_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{person}-{subtask_id}-{arm}.stdout.log"
    stderr_path = log_dir / f"{person}-{subtask_id}-{arm}.stderr.log"
    command = [
        sys.executable,
        "-m",
        "contextweave.metrics.workspace.run",
        "--person",
        person,
        "--input-root",
        str(run_root / person),
        "--recall-dirname",
        arm,
        "--person-root",
        str(BENCH_ROOT / "data" / "tasks"),
        "--full-dirname",
        "task",
        "--metrics-root",
        str(job_metrics),
        "--eval-root",
        str(job_metrics / "workspace"),
        "--output",
        str(job_metrics / "workspace-results.jsonl"),
        "--summary-output",
        str(job_metrics / "workspace-summary.json"),
        "--subtasks",
        subtask_id,
        "--image-prefix",
        f"membench-memory-agent-{memory_system}-with_recall-{person}",
        "--reference-image-prefix",
        f"reference-{person}",
        "--codex-base-url",
        base_url,
        "--codex-api-key",
        api_key,
        "--codex-model",
        model,
        "--codex-reasoning-effort",
        "medium",
        "--timeout-sec",
        str(timeout_seconds),
    ]
    if force:
        command.append("--force")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(BENCH_ROOT)
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        result = subprocess.run(
            command,
            cwd=BENCH_ROOT,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            text=True,
            check=False,
        )
    return {
        "person": person,
        "subtask_id": subtask_id,
        "arm": arm,
        "returncode": result.returncode,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score retained ContextWeave MiniClaw workspaces")
    parser.add_argument("--run-id", default="core-4-graded")
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--group", choices=("core", "overflow_stress", "all"), default="core")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--arms", default="retrieval,concat")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    cases = list(selection.get("core") or [])
    if args.group == "overflow_stress":
        cases = list(selection.get("overflow_stress") or [])
    elif args.group == "all":
        cases.extend(selection.get("overflow_stress") or [])
    if args.case:
        selected = set(args.case)
        cases = [case for case in cases if f"{case['person']}:{case['subtask_id']}" in selected]
    arms = [arm.strip() for arm in args.arms.split(",") if arm.strip()]
    if not cases or any(arm not in {"retrieval", "concat"} for arm in arms):
        raise SystemExit("No cases selected or invalid arm")

    env_values = read_env_file(ROOT / ".env")
    base_url = env_values.get("MINICLAW_PRIMARY_BASE_URL", "")
    api_key = env_values.get("MINICLAW_PRIMARY_API_KEY", "")
    if not base_url or not api_key:
        raise SystemExit("Missing MINICLAW_PRIMARY_BASE_URL or MINICLAW_PRIMARY_API_KEY in .env")

    run_root = DEFAULT_RESULTS / args.run_id
    metrics_root = run_root / "_official_workspace_metrics"
    jobs = [(case, arm) for case in cases for arm in arms]
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(
                score_job,
                case,
                arm,
                run_root=run_root,
                metrics_root=metrics_root,
                base_url=base_url,
                api_key=api_key,
                model=args.model,
                timeout_seconds=args.timeout_seconds,
                force=args.force,
            ): (case, arm)
            for case, arm in jobs
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)

    (metrics_root / "launcher-summary.json").write_text(
        json.dumps(sorted(results, key=lambda item: (item["person"], item["subtask_id"], item["arm"])), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(metrics_root)


if __name__ == "__main__":
    main()
