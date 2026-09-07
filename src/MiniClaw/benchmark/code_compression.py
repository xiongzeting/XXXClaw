from __future__ import annotations

"""Native MiniClaw adapter for the Dasein Code-Compression Bench.

The adapter deliberately keeps the coding scaffold, model, prompt, runtime,
approval policy, and turn cap fixed.  The named arm changes only MiniClaw's
working-context compaction strategy.
"""

import argparse
import asyncio
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from MiniClaw.coding_agent.approval.config import ApprovalSettings
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.runtime import load_runtime_settings
from MiniClaw.llm.config import load_llm_settings
from MiniClaw.llm.env_file import merged_environment, read_env_file
from MiniClaw.llm.factory import create_model_client, model_profile_from_settings
from MiniClaw.trace.store import add_usage, read_trace_records, zero_usage


ArmName = Literal["legacy-summary-recent", "layered-current"]
ARMS: tuple[ArmName, ...] = ("legacy-summary-recent", "layered-current")
DEFAULT_DATASET = "princeton-nlp/SWE-bench_Verified"
DEFAULT_SPLIT = "test"
DEFAULT_RESULTS_ROOT = Path(".aster/benchmarks/results/code-compression")
DEFAULT_WORKSPACE_ROOT = Path(".aster/benchmarks/workspaces/code-compression")


@dataclass(slots=True, frozen=True)
class Instance:
    instance_id: str
    problem_statement: str
    repo: str = ""
    base_commit: str = ""
    version: str = ""


def arm_environment(base: Mapping[str, str], arm: ArmName) -> dict[str, str]:
    """Return the frozen A/B context policy without mutating process state."""

    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; choose one of: {', '.join(ARMS)}")
    environment = dict(base)
    environment.update(
        {
            "MINICLAW_COMPACTION_ENABLED": "true",
            "MINICLAW_COMPACTION_STRATEGY": arm,
            "MINICLAW_COMPACTION_SOFT_TRIGGER_TOKENS": "80000",
            "MINICLAW_COMPACTION_HARD_TRIGGER_TOKENS": "100000",
            "MINICLAW_COMPACTION_TARGET_TOKENS": "30000",
            "MINICLAW_COMPACTION_KEEP_RECENT_TOKENS": "20000",
            "MINICLAW_CONTEXT_ARTIFACT_THRESHOLD_BYTES": str(16 * 1024),
            "MINICLAW_PROGRESSIVE_COMPACTION_ENABLED": (
                "false" if arm == "legacy-summary-recent" else "true"
            ),
        }
    )
    return environment


def build_task_prompt(instance: Instance) -> str:
    return (
        "Fix the repository issue below. Inspect the code with the available tools, implement "
        "the smallest correct fix, and run relevant tests or another concrete verification. "
        "Do not only describe a solution; make the changes in the workspace. End with a concise "
        "summary of the change and verification.\n\n"
        f"SWE-bench instance: {instance.instance_id}\n\n"
        f"<problem_statement>\n{instance.problem_statement.strip()}\n</problem_statement>"
    )


def resolve_instance(
    instance_id: str,
    *,
    instance_file: str | Path | None = None,
    problem_file: str | Path | None = None,
    dataset: str = DEFAULT_DATASET,
    split: str = DEFAULT_SPLIT,
) -> Instance:
    if problem_file is not None:
        return Instance(instance_id, Path(problem_file).read_text(encoding="utf-8"))
    if instance_file is not None:
        rows = _read_instance_rows(Path(instance_file))
    else:
        try:
            from datasets import load_dataset  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "Install the benchmark extra (datasets), or pass --instance-file/--problem-file"
            ) from exc
        rows = load_dataset(dataset, split=split)
    for row in rows:
        if str(row.get("instance_id") or "") != instance_id:
            continue
        statement = str(row.get("problem_statement") or "").strip()
        if not statement:
            raise ValueError(f"instance {instance_id} has no problem_statement")
        return Instance(
            instance_id=instance_id,
            problem_statement=statement,
            repo=str(row.get("repo") or ""),
            base_commit=str(row.get("base_commit") or ""),
            version=str(row.get("version") or ""),
        )
    raise ValueError(f"instance {instance_id!r} was not found")


def _read_instance_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.casefold() == ".jsonl":
        return [
            value
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and isinstance((value := json.loads(line)), dict)
        ]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("instances", "rows", "data"):
            values = payload.get(key)
            if isinstance(values, list):
                return [item for item in values if isinstance(item, dict)]
        return [payload]
    raise ValueError(f"unsupported instance file: {path}")


def ensure_source_repository(
    instance: Instance,
    *,
    source_workspace: str | Path | None,
    cache_root: str | Path,
) -> tuple[Path, str | None]:
    """Resolve an explicit base checkout or a reusable GitHub clone cache."""

    if source_workspace is not None:
        source = Path(source_workspace).resolve(strict=True)
        revision = instance.base_commit or None
        if revision:
            present = subprocess.run(
                ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
                cwd=source,
                capture_output=True,
                text=True,
                check=False,
            )
            if present.returncode != 0:
                raise ValueError(
                    f"explicit source workspace does not contain base commit {revision}"
                )
        return source, revision
    if not instance.repo or not instance.base_commit:
        raise ValueError(
            "dataset metadata must include repo/base_commit when --source-workspace is omitted"
        )
    cache = Path(cache_root).resolve() / instance.repo.replace("/", "__")
    if not (cache / ".git").is_dir():
        cache.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--quiet", f"https://github.com/{instance.repo}.git", str(cache)])
    present = subprocess.run(
        ["git", "cat-file", "-e", f"{instance.base_commit}^{{commit}}"],
        cwd=cache,
        capture_output=True,
        text=True,
        check=False,
    )
    if present.returncode != 0:
        _run(["git", "fetch", "--quiet", "origin", instance.base_commit], cwd=cache)
    # Keep the fetched base reachable when cloning the cache into each arm.
    bench_ref = f"refs/heads/miniclaw-bench-{instance.base_commit[:12]}"
    _run(["git", "update-ref", bench_ref, instance.base_commit], cwd=cache)
    return cache, instance.base_commit


def prepare_arm_workspace(
    source: Path,
    destination: Path,
    *,
    revision: str | None = None,
) -> None:
    source = source.resolve(strict=True)
    if destination.exists():
        raise FileExistsError(f"arm workspace already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", "--quiet", "--no-hardlinks", str(source), str(destination)])
    if revision:
        _run(["git", "checkout", "--quiet", "--detach", revision], cwd=destination)
    _remove_git_history(destination)
    exclude = destination / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if ".aster/" not in existing.splitlines():
        exclude.write_text(existing.rstrip() + "\n.aster/\n", encoding="utf-8")


def _remove_git_history(workspace: Path) -> None:
    """Replace shared/future repository history with one synthetic base commit."""

    git_path = workspace / ".git"
    if git_path.is_dir():
        shutil.rmtree(git_path, onerror=_remove_readonly_git_entry)
    elif git_path.exists():
        git_path.unlink()
    _run(["git", "init", "--quiet"], cwd=workspace)
    _run(["git", "config", "user.email", "benchmark@miniclaw.local"], cwd=workspace)
    _run(["git", "config", "user.name", "MiniClaw Benchmark"], cwd=workspace)
    _run(["git", "add", "-A"], cwd=workspace)
    _run(["git", "commit", "--quiet", "-m", "SWE-bench base"], cwd=workspace)


def _remove_readonly_git_entry(function: Any, path: str, _error: Any) -> None:
    """Let Windows remove read-only pack/index files created by Git clones."""

    os.chmod(path, stat.S_IWRITE)
    function(path)


def capture_patch(workspace: Path) -> str:
    try:
        _run(["git", "add", "-A", "--", "."], cwd=workspace)
    except RuntimeError as exc:
        # A timed-out container command can be killed while Git is refreshing
        # the index, leaving a stale lock in this benchmark-only workspace.
        # The AgentLoop and its Docker command have fully stopped before patch
        # capture begins, so removing this exact lock and retrying is safe.
        lock = workspace / ".git" / "index.lock"
        if "index.lock" not in str(exc) or not lock.is_file():
            raise
        lock.unlink()
        _run(["git", "add", "-A", "--", "."], cwd=workspace)
    return _run(["git", "diff", "--cached", "--binary", "HEAD"], cwd=workspace).stdout


def trace_metrics(path: str | Path) -> dict[str, Any]:
    records = read_trace_records(path)
    usage = zero_usage()
    model_requests = []
    tool_calls = []
    compactions = []
    run_completed = []
    memory_retrievals = []
    for record in records:
        event_type = record.get("type")
        data = record.get("data") or {}
        if event_type == "model.request":
            model_requests.append(data)
            usage = add_usage(usage, data.get("usage") or {})
        elif event_type == "tool.call":
            tool_calls.append(data)
        elif event_type == "compaction.completed":
            compactions.append(data)
        elif event_type == "run.completed":
            run_completed.append(data)
        elif event_type == "memory.retrieval":
            memory_retrievals.append(data)

    layer_counts: dict[str, int] = {}
    old_tool_results = 0
    archive_chunks = 0
    archive_bytes = 0
    for event in compactions:
        details = event.get("details") or {}
        for layer in details.get("layers_applied") or []:
            layer_counts[str(layer)] = layer_counts.get(str(layer), 0) + 1
        old_tool_results += len(details.get("tool_artifacts") or [])
        archive_chunks += int(details.get("archive_indexed_chunks") or 0)
        archive = details.get("archive") or {}
        archive_bytes += int(archive.get("byte_size") or 0)

    live_artifacts = [
        call
        for call in tool_calls
        if isinstance(call.get("details"), dict)
        and isinstance(call["details"].get("context_artifact"), dict)
    ]
    agent_requests = [item for item in model_requests if item.get("purpose") == "agent"]
    compaction_requests = [item for item in model_requests if item.get("purpose") == "compaction"]
    return {
        "usage": usage,
        "model_requests": len(model_requests),
        "agent_model_requests": len(agent_requests),
        "compaction_model_requests": len(compaction_requests),
        "model_errors": sum(item.get("status") == "error" for item in model_requests),
        "model_retries": sum(int(item.get("retries") or 0) for item in model_requests),
        "tool_calls": len(tool_calls),
        "tool_errors": sum(item.get("status") == "error" for item in tool_calls),
        "peak_agent_input_tokens": max(
            (int((item.get("usage") or {}).get("input_tokens") or 0) for item in agent_requests),
            default=0,
        ),
        "compactions": len(compactions),
        "tokens_saved_by_compaction": sum(int(item.get("tokens_saved") or 0) for item in compactions),
        "compaction_model_latency_ms": sum(
            int(item.get("duration_ms") or 0) for item in compaction_requests
        ),
        "layers": layer_counts,
        "l1_live_artifacts": len(live_artifacts),
        "l1_live_artifact_bytes": sum(
            int(call["details"]["context_artifact"].get("byte_size") or 0)
            for call in live_artifacts
        ),
        "l2_old_tool_results_archived": old_tool_results,
        "l2_closed_history_archives": layer_counts.get("closed-history-archive", 0),
        "l2_archive_bytes": archive_bytes,
        "archive_indexed_chunks": archive_chunks,
        "archive_retrievals_with_injection": sum(
            int(item.get("injected_count") or 0) > 0 for item in memory_retrievals
        ),
        "run_duration_ms": sum(int(item.get("duration_ms") or 0) for item in run_completed),
        "run_status": run_completed[-1].get("status") if run_completed else "unknown",
        "stop_reason": run_completed[-1].get("stop_reason") if run_completed else "unknown",
    }


async def run_arm(
    *,
    instance: Instance,
    arm: ArmName,
    source_workspace: Path,
    workspace: Path,
    output_dir: Path,
    base_environment: Mapping[str, str],
    provider: str | None,
    model: str | None,
    base_url: str | None,
    sandbox: str,
    docker_image: str | None,
    max_turns: int,
    source_revision: str | None = None,
) -> dict[str, Any]:
    prepare_arm_workspace(source_workspace, workspace, revision=source_revision)
    output_dir.mkdir(parents=True, exist_ok=True)
    environment = arm_environment(base_environment, arm)
    settings = load_llm_settings(
        provider=provider,
        model_id=model,
        base_url=base_url,
        environment=environment,
    )
    runtime_settings = load_runtime_settings(
        environment,
        sandbox=sandbox,
        workspace_mode="direct",
        docker_image=docker_image,
    )
    assistant = CodingAssistant(
        model_client=create_model_client(settings),
        profile=model_profile_from_settings(settings),
        workspace=workspace,
        session_path=output_dir / "session" / "session.jsonl",
        session_id=f"{instance.instance_id}-{arm}",
        environment=environment,
        runtime_settings=runtime_settings,
        approval_settings=ApprovalSettings(policy="allow"),
        trace_channel="benchmark-code-compression",
        trace_provider=settings.provider,
    )
    assistant.loop.max_turns = max_turns
    prompt = build_task_prompt(instance)
    final_text = ""
    errors: list[str] = []
    started = time.perf_counter()
    async for event in assistant.run(prompt):
        if event.type == "run_finished" and event.message:
            final_text = event.message.content
        elif event.type == "error" and event.text:
            errors.append(event.text)
    elapsed = time.perf_counter() - started
    patch = capture_patch(workspace)
    patch_path = output_dir / "model.patch"
    patch_path.write_text(patch, encoding="utf-8")
    trace_path = output_dir / "session" / "trace.jsonl"
    assistant.trace_recorder.record(
        "benchmark.case.completed",
        {
            "benchmark": "Dasein Code-Compression Bench / MiniClaw native",
            "instance_id": instance.instance_id,
            "arm": arm,
            "prompt_sha256": _sha256(prompt),
            "patch_path": str(patch_path),
            "patch_bytes": len(patch.encode("utf-8")),
        },
    )
    metrics = trace_metrics(trace_path)
    result = {
        "benchmark": "Dasein Code-Compression Bench / MiniClaw native",
        "instance": asdict(instance),
        "arm": arm,
        "provider": settings.provider,
        "model": settings.model_id,
        "runtime": assistant.runtime.trace_metadata(),
        "max_turns": max_turns,
        "prompt_sha256": _sha256(prompt),
        "elapsed_seconds": round(elapsed, 3),
        "patch_path": str(patch_path),
        "patch_bytes": len(patch.encode("utf-8")),
        "patch_nonempty": bool(patch.strip()),
        "trace_path": str(trace_path),
        "final_text": final_text,
        "errors": errors,
        "metrics": metrics,
        "grade": {"status": "not_run"},
    }
    _write_json(output_dir / "result.json", result)
    return result


def grade_patch(
    instance_id: str,
    patch: str,
    *,
    dataset: str,
    split: str,
    output_dir: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Run the official SWE-bench Docker grader when its package is installed."""

    if not patch.strip():
        return {"status": "completed", "resolved": False, "ftp": 0.0, "error": "empty patch"}
    run_id = f"miniclaw_ccb_{uuid.uuid4().hex[:8]}"
    predictions = output_dir / "predictions.json"
    _write_json(
        predictions,
        [
            {
                "instance_id": instance_id,
                "model_name_or_path": "MiniClaw",
                "model_patch": patch,
            }
        ],
    )
    report_dir = output_dir / "grader"
    report_dir.mkdir(parents=True, exist_ok=True)
    grader_module = (
        "MiniClaw.benchmark.swebench_runner"
        if os.name == "nt"
        else "swebench.harness.run_evaluation"
    )
    command = [
        sys.executable,
        "-m",
        grader_module,
        "-d",
        dataset,
        "-s",
        split,
        "-i",
        instance_id,
        "-p",
        str(predictions),
        "--run_id",
        run_id,
        "--max_workers",
        "1",
        "--timeout",
        str(timeout_seconds),
        "--report_dir",
        str(report_dir),
    ]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=report_dir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 600,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "resolved": False,
            "ftp": 0.0,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "error": "official grader timed out",
        }
    report = _find_grade_report(
        report_dir / "logs" / "run_evaluation" / run_id,
        instance_id,
    )
    if report is None:
        return {
            "status": "error",
            "resolved": False,
            "ftp": 0.0,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "error": f"official grader produced no report: {(completed.stderr or '')[-500:]}",
        }
    tests = report.get("tests_status") or {}
    fail_to_pass = tests.get("FAIL_TO_PASS") or {}
    pass_to_pass = tests.get("PASS_TO_PASS") or {}
    ftp_passed = len(fail_to_pass.get("success") or [])
    ftp_failed = len(fail_to_pass.get("failure") or [])
    ptp_passed = len(pass_to_pass.get("success") or [])
    ptp_failed = len(pass_to_pass.get("failure") or [])
    ftp_total = ftp_passed + ftp_failed
    ptp_total = ptp_passed + ptp_failed
    return {
        "status": "completed",
        "resolved": bool(report.get("resolved")),
        "patch_successfully_applied": bool(report.get("patch_successfully_applied", True)),
        "infra_failure": bool(report.get("infra_failure", False)),
        "ftp": ftp_passed / ftp_total if ftp_total else 0.0,
        "fail_to_pass": {"passed": ftp_passed, "total": ftp_total},
        "pass_to_pass": {"passed": ptp_passed, "total": ptp_total},
        "regression_free": ptp_failed == 0,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "error": (
            "official grader could not apply the patch"
            if report.get("patch_successfully_applied") is False
            else "official grader reported an infrastructure failure"
            if report.get("infra_failure") is True
            else ""
        ),
        "grader_process_returncode": completed.returncode,
        "grader_process_warning": (
            (completed.stderr or "")[-500:] if completed.returncode != 0 else ""
        ),
    }


def _find_grade_report(root: Path, instance_id: str) -> dict[str, Any] | None:
    for path in [*root.rglob(f"{instance_id}/report.json"), *root.rglob("report.json")]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        value = payload.get(instance_id, payload) if isinstance(payload, dict) else None
        if isinstance(value, dict) and ("resolved" in value or "tests_status" in value):
            return value
    return None


async def run(args: argparse.Namespace) -> Path:
    if args.clean_workspaces and args.workspace_root:
        raise ValueError("--clean-workspaces cannot be combined with an explicit --workspace-root")
    instance = resolve_instance(
        args.instance_id,
        instance_file=args.instance_file,
        problem_file=args.problem_file,
        dataset=args.dataset,
        split=args.split,
    )
    env_path = Path(args.env_file) if args.env_file else Path.cwd() / ".env"
    environment = merged_environment(read_env_file(env_path) if env_path.is_file() else {})
    stamp = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    results_root = Path(args.output).resolve() / stamp / instance.instance_id
    workspaces_root = Path(args.workspace_root or DEFAULT_WORKSPACE_ROOT).resolve() / stamp / instance.instance_id
    source, source_revision = ensure_source_repository(
        instance,
        source_workspace=args.source_workspace,
        cache_root=args.repo_cache,
    )
    arms: list[ArmName] = list(dict.fromkeys(args.arm or ARMS))  # type: ignore[arg-type]
    results: list[dict[str, Any]] = []
    for arm in arms:
        result = await run_arm(
            instance=instance,
            arm=arm,
            source_workspace=source,
            workspace=workspaces_root / arm,
            output_dir=results_root / arm,
            base_environment=environment,
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
            sandbox=args.sandbox,
            docker_image=args.docker_image,
            max_turns=args.max_turns,
            source_revision=source_revision,
        )
        if args.grade:
            patch = Path(result["patch_path"]).read_text(encoding="utf-8")
            result["grade"] = grade_patch(
                instance.instance_id,
                patch,
                dataset=args.grader_dataset,
                split=args.split,
                output_dir=results_root / arm,
                timeout_seconds=args.grader_timeout,
            )
            _write_json(results_root / arm / "result.json", result)
        results.append(result)
        print(
            json.dumps(
                {
                    "instance_id": instance.instance_id,
                    "arm": arm,
                    "patch_nonempty": result["patch_nonempty"],
                    "input_tokens": result["metrics"]["usage"]["input_tokens"],
                    "peak_context": result["metrics"]["peak_agent_input_tokens"],
                    "compactions": result["metrics"]["compactions"],
                    "resolved": result["grade"].get("resolved"),
                    "errors": len(result["errors"]),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    summary = {
        "benchmark": "Dasein Code-Compression Bench / MiniClaw native",
        "run_id": stamp,
        "instance_id": instance.instance_id,
        "arms": results,
    }
    _write_json(results_root / "summary.json", summary)
    if args.clean_workspaces:
        shutil.rmtree(workspaces_root.parent.parent, ignore_errors=True)
    return results_root / "summary.json"


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{(completed.stderr or completed.stdout)[-1000:]}"
        )
    return completed


def _sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one SWE-bench task through MiniClaw compression arms"
    )
    parser.add_argument("--instance-id", required=True)
    parser.add_argument(
        "--source-workspace",
        help="Optional clean git checkout at the SWE-bench base state; otherwise use repo cache",
    )
    parser.add_argument(
        "--repo-cache",
        default=".aster/benchmarks/repo-cache",
        help="Reusable full repository clones used when --source-workspace is omitted",
    )
    parser.add_argument("--instance-file", help="Local SWE-bench JSON or JSONL")
    parser.add_argument("--problem-file", help="Plain-text problem statement (offline smoke runs)")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--grader-dataset", default="SWE-bench/SWE-bench_Verified")
    parser.add_argument("--split", default=DEFAULT_SPLIT)
    parser.add_argument("--arm", action="append", choices=ARMS)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--provider", choices=["primary", "openai", "zxcoding", "deepseek"], default="primary")
    parser.add_argument("--base-url")
    parser.add_argument("--env-file")
    parser.add_argument("--sandbox", default="docker")
    parser.add_argument("--docker-image")
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument("--grade", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--grader-timeout", type=int, default=1_800)
    parser.add_argument("--output", default=str(DEFAULT_RESULTS_ROOT))
    parser.add_argument("--workspace-root")
    parser.add_argument("--run-id")
    parser.add_argument("--clean-workspaces", action="store_true")
    return parser


def main() -> None:
    summary = asyncio.run(run(build_parser().parse_args()))
    print(summary)


if __name__ == "__main__":
    main()
