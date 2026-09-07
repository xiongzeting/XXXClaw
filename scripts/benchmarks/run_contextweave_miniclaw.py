from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BENCH_ROOT = ROOT / "external" / "benchmarks" / "ContextWeave-main"
DEFAULT_SELECTION = BENCH_ROOT / "generated" / "miniclaw-coding-memory-6.json"
DEFAULT_OUTPUT = ROOT / ".aster" / "benchmarks" / "results" / "contextweave"
AGENT_DEPS = ROOT / ".aster" / "benchmarks" / "contextweave-agent-deps"


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


def run_checked(command: list[str], *, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {command[0]}")


def image_exists(name: str) -> bool:
    return subprocess.run(
        ["docker", "image", "inspect", name],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def prepare_images(cases: list[dict[str, Any]]) -> None:
    for case in cases:
        source = str(case["source_image"])
        target = str(case["local_image"])
        if image_exists(target):
            continue
        run_checked(["docker", "pull", source])
        run_checked(["docker", "tag", source, target])


def run_case(
    case: dict[str, Any],
    arm: str,
    *,
    output_root: Path,
    environment: dict[str, str],
    timeout_seconds: int,
    commit_images: bool,
) -> dict[str, Any]:
    memory_system = "miniclaw-four-layer" if arm == "retrieval" else "miniclaw-concat"
    person = str(case["person"])
    subtask_id = str(case["subtask_id"])
    run_output = output_root / person / arm
    log_dir = output_root / "_launcher_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{person}-{subtask_id}-{arm}.stdout.log"
    stderr_path = log_dir / f"{person}-{subtask_id}-{arm}.stderr.log"
    command = [
        sys.executable,
        "-m",
        "contextweave.runner.run_memory_component",
        "--data-dir",
        str(BENCH_ROOT / "data" / "tasks"),
        "--run-output-dir",
        str(run_output),
        "--student",
        person,
        "--memory-system",
        memory_system,
        "--subtask-id",
        subtask_id,
        "--recall-mode",
        "with_recall",
        "--force",
        "--timeout-sec",
        str(timeout_seconds),
        "--inject-timeout-sec",
        "1800",
        "--recall-timeout-sec",
        "900",
        "--agent-command",
        "python3 -m MiniClaw.benchmark.contextweave agent",
        "--agent-source-dir",
        str(ROOT / "src"),
        "--agent-deps-dir",
        str(AGENT_DEPS),
    ]
    command.append("--commit-images" if commit_images else "--no-commit-images")
    started = datetime.now().isoformat(timespec="seconds")
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
        "started_at": started,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MiniClaw on the ContextWeave coding-memory subset")
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--group", choices=("core", "overflow_stress", "all"), default="core")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--arms", default="retrieval,concat")
    parser.add_argument("--case", action="append", default=[], help="optional person:subtask_id filter")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--skip-image-prepare", action="store_true")
    parser.add_argument(
        "--commit-images",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="retain completed workspaces as Docker images for official workspace grading",
    )
    args = parser.parse_args()

    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    cases = list(selection.get("core") or [])
    if args.group == "overflow_stress":
        cases = list(selection.get("overflow_stress") or [])
    elif args.group == "all":
        cases.extend(selection.get("overflow_stress") or [])
    if args.case:
        selected_keys = set(args.case)
        cases = [case for case in cases if f"{case['person']}:{case['subtask_id']}" in selected_keys]
    arms = [item.strip() for item in args.arms.split(",") if item.strip()]
    if not cases or any(arm not in {"retrieval", "concat"} for arm in arms):
        raise SystemExit("No cases selected or invalid arm")
    if not AGENT_DEPS.exists():
        raise SystemExit(f"Missing container dependencies: {AGENT_DEPS}")

    if not args.skip_image_prepare:
        prepare_images(cases)

    environment = os.environ.copy()
    environment.update(read_env_file(ROOT / ".env"))
    environment["MINICLAW_PROVIDER"] = "primary"
    environment["MINICLAW_PRIMARY_MODEL"] = "gpt-5.6-luna"
    environment["MINICLAW_LLM_FALLBACKS"] = ""
    environment["PYTHONIOENCODING"] = "utf-8"
    python_path = os.pathsep.join((str(ROOT / "src"), str(BENCH_ROOT)))
    environment["PYTHONPATH"] = python_path

    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root = DEFAULT_OUTPUT / run_id
    output_root.mkdir(parents=True, exist_ok=True)
    jobs = [(case, arm) for case in cases for arm in arms]
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(
                run_case,
                case,
                arm,
                output_root=output_root,
                environment=environment,
                timeout_seconds=args.timeout_seconds,
                commit_images=args.commit_images,
            ): (case, arm)
            for case, arm in jobs
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)

    summary = {
        "benchmark": "ContextWeave",
        "selection": str(args.selection.resolve()),
        "group": args.group,
        "workers": args.workers,
        "model": "gpt-5.6-luna",
        "arms": arms,
        "commit_images": args.commit_images,
        "results": sorted(results, key=lambda item: (item["person"], item["subtask_id"], item["arm"])),
    }
    (output_root / "launcher_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(output_root)


if __name__ == "__main__":
    main()
