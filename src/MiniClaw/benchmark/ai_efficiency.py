from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import shutil
import stat
import subprocess
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from MiniClaw.coding_agent.approval import ApprovalSettings
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.runtime import load_runtime_settings
from MiniClaw.llm.config import load_llm_settings
from MiniClaw.llm.env_file import merged_environment, read_env_file
from MiniClaw.llm.factory import create_model_client, model_profile_from_settings
from MiniClaw.trace.store import TraceRecorder, add_usage, read_trace_records, zero_usage


PROJECT_ROOT = Path(__file__).resolve().parents[3]
UPSTREAM_ROOT = PROJECT_ROOT / "external" / "benchmarks" / "ai-efficiency-benchmark"
DEFAULT_SYNTH_ROOT = PROJECT_ROOT / ".aster" / "benchmarks" / "ai-efficiency" / "fixtures" / "synth"
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / ".aster" / "benchmarks" / "results" / "ai-efficiency" / "tool-compaction-20"
)
MANIFEST_PATH = UPSTREAM_ROOT / "deliverables" / "MANIFEST_COMBINED.jsonl"


# The complete upstream distribution for the five families chosen before the run:
# 4 root-cause logs + 4 multi-file debugging + 4 shell pipelines
# + 2 exact-preservation controls + 6 long sessions = 20 tasks.
TOOL_COMPACTION_20 = (
    "log_small_easy",
    "log_ci_medium",
    "log_prod_hard",
    "s_log_build_error",
    "dbg_multi_orders",
    "dbg_multi_batch",
    "s_bug_env_precedence",
    "s_bug_cache_stale",
    "pipe_redirect_bytes",
    "pipe_count_defs",
    "s_pipe_sort_order",
    "s_cmdsub_count",
    "exact_block_lines",
    "s_neg_registry_line",
    "long_synth_ops",
    "long_click_audit",
    "long_synth_incident",
    "f_long_audit",
    "g_long_audit",
    "c_long_types",
)


ARM_SETTINGS: dict[str, dict[str, str]] = {
    "full": {
        "MINICLAW_CONTEXT_ARTIFACT_THRESHOLD_BYTES": str(16 * 1024),
        "MINICLAW_CONTEXT_ARTIFACT_PREVIEW_CHARS": "4000",
    },
    "artifact-off": {
        # The normal Bash/Read safety limits still apply, but the WorkingContext
        # no longer replaces a returned result with an Artifact reference.
        "MINICLAW_CONTEXT_ARTIFACT_THRESHOLD_BYTES": "1000000000",
        "MINICLAW_CONTEXT_ARTIFACT_PREVIEW_CHARS": "4000",
    },
}


def _load_manifest(path: Path = MANIFEST_PATH) -> dict[str, dict[str, Any]]:
    tasks: dict[str, dict[str, Any]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        task = json.loads(raw_line)
        tasks[str(task["task_id"])] = task
    return tasks


def selected_tasks(
    manifest: Mapping[str, dict[str, Any]],
    task_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    requested = list(task_ids or TOOL_COMPACTION_20)
    if len(requested) != len(set(requested)):
        raise ValueError("task ids must be unique")
    unknown_selection = [task_id for task_id in requested if task_id not in TOOL_COMPACTION_20]
    if unknown_selection:
        raise ValueError(
            "task ids are outside the fixed tool-compaction distribution: "
            + ", ".join(unknown_selection)
        )
    missing = [task_id for task_id in requested if task_id not in manifest]
    if missing:
        raise ValueError(f"Upstream manifest is missing selected tasks: {', '.join(missing)}")
    return [manifest[task_id] for task_id in requested]


def _load_id_file(path: str | None) -> list[str]:
    if not path:
        return []
    return [
        value
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines()
        if (value := line.strip()) and not value.startswith("#")
    ]


def _checked_run(command: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n{result.stderr[-2000:]}"
        )
    return result


def _copy_tree_contents(source: Path, destination: Path) -> None:
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        if ".git" in relative.parts:
            continue
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _remove_readonly_tree(path: Path) -> None:
    def make_writable_and_retry(function: Any, value: str, _error: Any) -> None:
        os.chmod(value, stat.S_IWRITE)
        function(value)

    shutil.rmtree(path, onerror=make_writable_and_retry)


def prepare_workdir(
    task: Mapping[str, Any],
    destination: Path,
    *,
    synth_root: Path,
) -> None:
    destination = destination.resolve()
    if destination.exists():
        _remove_readonly_tree(destination)
    destination.mkdir(parents=True)
    if task["repo"] == "synth":
        source = synth_root / "base"
    else:
        source = UPSTREAM_ROOT / "fixtures" / "repos" / str(task["repo"])
    if not source.is_dir():
        raise FileNotFoundError(f"Benchmark fixture is missing: {source}")
    _copy_tree_contents(source, destination)
    overlay_name = task.get("overlay")
    if overlay_name:
        overlay = synth_root / "overlays" / str(overlay_name)
        if not overlay.is_dir():
            raise FileNotFoundError(f"Benchmark overlay is missing: {overlay}")
        _copy_tree_contents(overlay, destination)

    _checked_run(["git", "init", "-q"], cwd=destination)
    _checked_run(["git", "config", "user.name", "MiniClaw Benchmark"], cwd=destination)
    _checked_run(["git", "config", "user.email", "benchmark@miniclaw.local"], cwd=destination)
    exclude = destination / ".git" / "info" / "exclude"
    with exclude.open("a", encoding="utf-8") as handle:
        handle.write("\n.aster/\n")
    _checked_run(["git", "add", "-A"], cwd=destination)
    _checked_run(["git", "commit", "-qm", "benchmark fixture"], cwd=destination)


def changed_files(workdir: Path) -> list[str]:
    output = _checked_run(["git", "status", "--porcelain"], cwd=workdir).stdout
    files = [line[3:].strip() for line in output.splitlines() if line.strip()]
    return [
        path
        for path in files
        if not path.startswith(".aster/")
        and "__pycache__" not in path
        and ".pytest_cache" not in path
        and not path.endswith(".pyc")
    ]


def _docker_shell(workdir: Path, image: str, command: str, timeout: float) -> subprocess.CompletedProcess[str]:
    mount = f"{workdir.resolve()}:/workspace"
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            mount,
            "-w",
            "/workspace",
            image,
            "bash",
            "-lc",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def dynamic_expected(task: Mapping[str, Any], workdir: Path, image: str) -> str | None:
    command = (task.get("judge") or {}).get("dynamic_count")
    if not command:
        return None
    result = _docker_shell(workdir, image, str(command), 120)
    if result.returncode != 0:
        raise RuntimeError(f"dynamic_count failed: {result.stderr[-1000:]}")
    return result.stdout.strip()


def judge_task(
    task: Mapping[str, Any],
    workdir: Path,
    result_text: str,
    *,
    synth_root: Path,
    image: str,
    expected_dynamic: str | None,
) -> tuple[bool, str]:
    changed = changed_files(workdir)
    forbidden = list(task.get("forbidden_files") or [])
    allowed = set(task.get("allowed_files") or [])
    if forbidden == ["*"] and changed:
        return False, "unrelated_edits"
    for path in changed:
        is_forbidden = any(
            path == item or (item.endswith("/") and path.startswith(item))
            for item in forbidden
            if item != "*"
        )
        if is_forbidden:
            return False, "forbidden_file_changed"
        if allowed and path not in allowed and forbidden != ["*"]:
            return False, "out_of_scope_edit"

    rules = task["judge"]
    if rules.get("cmd"):
        checked = _docker_shell(workdir, image, str(rules["cmd"]), 240)
        if checked.returncode != 0:
            return False, "judge_cmd_failed"
    if rules.get("file_check"):
        checked = _docker_shell(workdir, image, str(rules["file_check"]), 120)
        if checked.returncode != 0:
            return False, "artifact_check_failed"
    if rules.get("dynamic_count") and expected_dynamic is not None:
        if not re.search(rf"\b{re.escape(expected_dynamic)}\b", result_text or ""):
            return False, f"dynamic_count_missing:{expected_dynamic}"
    for expression in rules.get("regexes") or []:
        if not re.search(str(expression), result_text or ""):
            return False, f"missing_answer_part:{str(expression)[:30]}"
    if rules.get("block"):
        ground_truth = json.loads((synth_root / "ground_truth.json").read_text(encoding="utf-8"))
        for line in ground_truth[str(rules["block"])]:
            if line not in (result_text or ""):
                return False, "exact_block_line_missing"
    return True, "ok"


@contextmanager
def patched_environment(values: Mapping[str, str]) -> Iterable[None]:
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _trace_metrics(trace_path: Path, run_id: str) -> dict[str, Any]:
    records = [record for record in read_trace_records(trace_path) if record.get("run_id") == run_id]
    usage = zero_usage()
    model_requests = [record for record in records if record["type"] == "model.request"]
    tool_calls = [record for record in records if record["type"] == "tool.call"]
    compactions = [record for record in records if record["type"] == "compaction.completed"]
    for record in model_requests:
        usage = add_usage(usage, record["data"].get("usage") or {})

    signatures: dict[str, int] = {}
    artifactized = 0
    full_output_files = 0
    artifact_recovery_calls = 0
    tool_names: dict[str, int] = {}
    for record in tool_calls:
        data = record["data"]
        tool_name = str(data.get("tool_name") or "")
        tool_names[tool_name] = tool_names.get(tool_name, 0) + 1
        signature = tool_name + "\0" + json.dumps(
            data.get("arguments") or {}, ensure_ascii=False, sort_keys=True
        )
        signatures[signature] = signatures.get(signature, 0) + 1
        details = data.get("details") or {}
        if details.get("context_artifact"):
            artifactized += 1
        if details.get("fullOutputPath"):
            full_output_files += 1
        argument_text = json.dumps(data.get("arguments") or {}, ensure_ascii=False)
        if ".aster/context-artifacts/" in argument_text or ".aster/tool-output/" in argument_text:
            artifact_recovery_calls += 1

    return {
        "usage": usage,
        "model_requests": len(model_requests),
        "model_errors": sum(record["data"].get("status") == "error" for record in model_requests),
        "tool_calls": len(tool_calls),
        "tool_errors": sum(record["data"].get("status") == "error" for record in tool_calls),
        "tool_names": tool_names,
        "duplicate_tool_calls": sum(max(0, count - 1) for count in signatures.values()),
        "artifactized_tool_results": artifactized,
        "runtime_full_output_files": full_output_files,
        "artifact_recovery_calls": artifact_recovery_calls,
        "compactions": len(compactions),
        "tokens_saved_by_compaction": sum(
            max(
                0,
                int(record["data"].get("tokens_before") or 0)
                - int(record["data"].get("tokens_after") or 0),
            )
            for record in compactions
        ),
        "compaction_events": [record["data"] for record in compactions],
    }


async def run_agent(
    task: Mapping[str, Any],
    arm: str,
    workdir: Path,
    *,
    environment: Mapping[str, str],
    image: str,
) -> tuple[str, str, dict[str, Any], str]:
    session_dir = workdir / ".aster" / "benchmark"
    session_path = session_dir / "session.jsonl"
    arm_environment = {
        "MINICLAW_COMPACTION_ENABLED": "true",
        "MINICLAW_PROGRESSIVE_COMPACTION_ENABLED": "true",
        "MINICLAW_COMPACTION_SOFT_TRIGGER_TOKENS": "80000",
        "MINICLAW_COMPACTION_HARD_TRIGGER_TOKENS": "100000",
        "MINICLAW_COMPACTION_TARGET_TOKENS": "30000",
        "MINICLAW_COMPACTION_KEEP_RECENT_TOKENS": "20000",
        **ARM_SETTINGS[arm],
    }
    runtime = load_runtime_settings(
        environment,
        sandbox=f"docker:{image}",
        workspace_mode="direct",
    )
    settings = load_llm_settings(environment=environment)
    with patched_environment(arm_environment):
        assistant = CodingAssistant(
            model_client=create_model_client(settings),
            profile=model_profile_from_settings(settings),
            workspace=workdir,
            session_path=session_path,
            session_id=f"{task['task_id']}-{arm}",
            environment=environment,
            runtime_settings=runtime,
            approval_settings=ApprovalSettings(policy="allow", timeout_seconds=1),
            trace_channel="benchmark",
            trace_provider=settings.provider,
        )
        assistant.loop.max_turns = int(task["max_turns"])
        final_text = ""
        error = ""
        run_id = ""

        async def collect() -> None:
            nonlocal final_text, error, run_id
            async for event in assistant.run(str(task["prompt"])):
                if event.type == "run_finished":
                    if event.message and event.message.content.strip():
                        final_text = event.message.content.strip()
                    if event.details:
                        run_id = str(event.details.get("run_id") or run_id)
                        error = str(event.details.get("error") or error)
                elif event.type == "error" and event.text:
                    error = event.text

        try:
            await asyncio.wait_for(collect(), timeout=float(task["timeout"]))
        except TimeoutError:
            assistant.cancel("AI Efficiency Benchmark task timeout")
            error = "timeout"
        if not run_id:
            records = read_trace_records(session_dir / "trace.jsonl")
            candidate_ids = [str(record.get("run_id") or "") for record in records]
            run_id = next((item for item in reversed(candidate_ids) if item), "")
        metrics = _trace_metrics(session_dir / "trace.jsonl", run_id)
        return final_text, error, metrics, run_id


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def _load_results(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    output: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            output.append(json.loads(line))
    return output


def write_summary(results: Sequence[Mapping[str, Any]], path: Path) -> None:
    arms: dict[str, dict[str, Any]] = {}
    for arm in ARM_SETTINGS:
        members = [item for item in results if item.get("arm") == arm]
        successful = [item for item in members if item.get("success") is True]
        usage = zero_usage()
        for item in members:
            usage = add_usage(usage, (item.get("metrics") or {}).get("usage") or {})
        arms[arm] = {
            "runs": len(members),
            "successes": len(successful),
            "success_rate": len(successful) / len(members) if members else None,
            "usage": usage,
            "wall_seconds": round(sum(float(item.get("wall_seconds") or 0) for item in members), 3),
            "tool_calls": sum(int((item.get("metrics") or {}).get("tool_calls") or 0) for item in members),
            "duplicate_tool_calls": sum(
                int((item.get("metrics") or {}).get("duplicate_tool_calls") or 0)
                for item in members
            ),
            "artifactized_tool_results": sum(
                int((item.get("metrics") or {}).get("artifactized_tool_results") or 0)
                for item in members
            ),
            "artifact_recovery_calls": sum(
                int((item.get("metrics") or {}).get("artifact_recovery_calls") or 0)
                for item in members
            ),
            "compactions": sum(
                int((item.get("metrics") or {}).get("compactions") or 0) for item in members
            ),
        }
    paired_ids = {
        item["task_id"]
        for item in results
        if all(
            any(other["task_id"] == item["task_id"] and other["arm"] == arm for other in results)
            for arm in ARM_SETTINGS
        )
    }
    paired = {
        arm: sum(
            item.get("success") is True
            for item in results
            if item["arm"] == arm and item["task_id"] in paired_ids
        )
        for arm in ARM_SETTINGS
    }
    payload = {
        "benchmark": "PointFiveLabs/ai-efficiency-benchmark",
        "selection": "tool-compaction-20",
        "tasks": len(TOOL_COMPACTION_20),
        "completed_runs": len(results),
        "arms": arms,
        "paired_tasks": len(paired_ids),
        "paired_successes": paired,
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


async def run(args: argparse.Namespace) -> int:
    output_root = Path(args.output).resolve()
    synth_root = Path(args.synth_root).resolve()
    env_path = Path(args.env_file).resolve()
    environment = merged_environment(read_env_file(env_path))
    if args.provider:
        environment["MINICLAW_PROVIDER"] = args.provider
    if args.model:
        environment["MINICLAW_MODEL"] = args.model
    environment["MINICLAW_DOCKER_IMAGE"] = args.docker_image
    environment["MINICLAW_SANDBOX"] = f"docker:{args.docker_image}"
    environment["MINICLAW_WORKSPACE_MODE"] = "direct"
    environment["MINICLAW_DOCKER_NETWORK"] = "none"
    environment["MINICLAW_APPROVAL_POLICY"] = "allow"

    manifest = _load_manifest()
    explicit_ids = [*args.task_id, *_load_id_file(args.task_id_file)]
    tasks = selected_tasks(manifest, explicit_ids or None)
    if args.limit is not None:
        tasks = tasks[: args.limit]
    selection = {
        "upstream": "PointFiveLabs/ai-efficiency-benchmark",
        "upstream_commit": _checked_run(
            ["git", "rev-parse", "HEAD"], cwd=UPSTREAM_ROOT
        ).stdout.strip(),
        "task_ids": [str(task["task_id"]) for task in tasks],
        "arms": ARM_SETTINGS,
        "provider": load_llm_settings(environment=environment).provider,
        "model": load_llm_settings(environment=environment).model_id,
        "docker_image": args.docker_image,
        "seed": args.seed,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "selection.json").write_text(
        json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    results_path = output_root / "results.jsonl"
    results = _load_results(results_path) if args.resume else []
    completed = {(str(item["task_id"]), str(item["arm"])) for item in results}

    for position, task in enumerate(tasks, start=1):
        arms = list(ARM_SETTINGS)
        random.Random(args.seed + position).shuffle(arms)
        for arm in arms:
            key = (str(task["task_id"]), arm)
            if key in completed:
                continue
            case_root = output_root / "cases" / str(task["task_id"]) / arm
            workdir = case_root / "work"
            prepare_workdir(task, workdir, synth_root=synth_root)
            expected = dynamic_expected(task, workdir, args.docker_image)
            started = time.perf_counter()
            final_text, error, metrics, trace_run_id = await run_agent(
                task,
                arm,
                workdir,
                environment=environment,
                image=args.docker_image,
            )
            wall_seconds = time.perf_counter() - started
            try:
                success, reason = judge_task(
                    task,
                    workdir,
                    final_text,
                    synth_root=synth_root,
                    image=args.docker_image,
                    expected_dynamic=expected,
                )
            except Exception as exc:
                success, reason = False, f"judge_exception:{type(exc).__name__}:{exc}"
            if error:
                success = False
                reason = f"agent_error:{error}"
            diff = _checked_run(["git", "diff"], cwd=workdir).stdout
            if diff:
                (case_root / "work.diff").write_text(diff[:400_000], encoding="utf-8")
            result = {
                "run_id": uuid.uuid4().hex,
                "task_id": task["task_id"],
                "family": task["family"],
                "difficulty": task["difficulty"],
                "repo": task["repo"],
                "repo_rev": task["repo_rev"],
                "arm": arm,
                "success": success,
                "failure_reason": None if success else reason,
                "result_text": final_text,
                "error": error,
                "wall_seconds": round(wall_seconds, 3),
                "metrics": metrics,
            }
            if trace_run_id:
                TraceRecorder(
                    workdir / ".aster" / "benchmark",
                    "benchmark",
                    f"{task['task_id']}-{arm}",
                ).record(
                    "benchmark.case.completed",
                    {
                        "benchmark": "PointFiveLabs/ai-efficiency-benchmark",
                        "task_id": task["task_id"],
                        "family": task["family"],
                        "difficulty": task["difficulty"],
                        "question": task["prompt"],
                        "output": final_text,
                        "success": success,
                        "failure_reason": None if success else reason,
                        "judge_spec": task.get("judge"),
                        "expected_dynamic": expected,
                        "metrics": metrics,
                    },
                    run_id=trace_run_id,
                )
            _append_jsonl(results_path, result)
            results.append(result)
            completed.add(key)
            write_summary(results, output_root / "summary.json")
            print(
                json.dumps(
                    {
                        "task": task["task_id"],
                        "family": task["family"],
                        "arm": arm,
                        "success": success,
                        "reason": reason,
                        "tool_calls": metrics["tool_calls"],
                        "artifacts": metrics["artifactized_tool_results"],
                        "compactions": metrics["compactions"],
                        "input_tokens": metrics["usage"]["input_tokens"],
                        "wall_seconds": round(wall_seconds, 1),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run MiniClaw on the upstream AI Efficiency tool-compaction 20-task slice"
    )
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument("--provider", default="primary")
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--docker-image", default="miniclaw-runtime:py313-bench")
    parser.add_argument("--synth-root", default=str(DEFAULT_SYNTH_ROOT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--seed", type=int, default=20260903)
    parser.add_argument("--limit", type=int, choices=range(1, len(TOOL_COMPACTION_20) + 1))
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--task-id-file", help="UTF-8 file containing one task id per line")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    return parser


def main() -> None:
    raise SystemExit(asyncio.run(run(build_parser().parse_args())))


if __name__ == "__main__":
    main()
