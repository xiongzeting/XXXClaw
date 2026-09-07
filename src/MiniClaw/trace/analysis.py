from __future__ import annotations

import hashlib
import html
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .store import TraceRecorder, add_usage, read_trace_records, zero_usage


def group_trace_runs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runs: dict[str, dict[str, Any]] = {}
    for record in records:
        run_id = record.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        run = runs.setdefault(
            run_id,
            {
                "trace_id": record.get("trace_id", ""),
                "run_id": run_id,
                "started": None,
                "completed": None,
                "model_requests": [],
                "model_transports": [],
                "tool_calls": [],
                "approvals": [],
                "compactions": [],
                "goals": [],
                "benchmark_starts": [],
                "benchmark_cases": [],
            },
        )
        event_type = record.get("type")
        if event_type == "run.started":
            run["started"] = record
        elif event_type == "run.completed":
            run["completed"] = record
        elif event_type == "model.request":
            run["model_requests"].append(record)
        elif event_type == "model.transport":
            run["model_transports"].append(record)
        elif event_type == "tool.call":
            run["tool_calls"].append(record)
        elif event_type == "approval.decision":
            run["approvals"].append(record)
        elif isinstance(event_type, str) and event_type.startswith("compaction."):
            run["compactions"].append(record)
        elif event_type == "goal.snapshot":
            run["goals"].append(record)
        elif event_type == "benchmark.case.started":
            run["benchmark_starts"].append(record)
        elif event_type == "benchmark.case.completed":
            run["benchmark_cases"].append(record)
    return sorted(
        runs.values(),
        key=lambda item: ((item.get("started") or {}).get("timestamp") or ""),
    )


def load_trace_runs(session_dir: str | Path) -> list[dict[str, Any]]:
    return group_trace_runs(read_trace_records(Path(session_dir) / "trace.jsonl"))


def create_eval_case(
    session_dir: str | Path,
    output_path: str | Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    session = Path(session_dir).resolve()
    run = _select_run(load_trace_runs(session), run_id, prefer_failure=True)
    started = run.get("started")
    benchmark_record = _latest_benchmark_case(run, failure_only=True)
    if not started and not benchmark_record:
        raise ValueError(f"Trace run {run['run_id']} has no run.started event")
    started_data = started.get("data") or {} if started else {}
    benchmark_data = (benchmark_record or {}).get("data") or {}
    benchmark_start = (run.get("benchmark_starts") or [{}])[-1].get("data") or {}
    prompt = str(benchmark_data.get("question") or started_data.get("request") or "").strip()
    failure = describe_failure(run)
    digest = hashlib.sha256(f"{run['trace_id']}:{run['run_id']}".encode()).hexdigest()[:10]
    case = {
        "version": 1,
        "id": f"trace-{_slugify(prompt)[:40] or 'failure'}-{digest}",
        "title": f"Trace regression: {_truncate(' '.join(prompt.split()), 100)}",
        "category": "trace-regression",
        "source": {
            "type": "benchmark_failure" if benchmark_record else "real_failure",
            "session_dir": str(session),
            "trace_id": run["trace_id"],
            "run_id": run["run_id"],
            "benchmark": benchmark_data.get("benchmark") or benchmark_start.get("benchmark"),
            "case_id": _benchmark_case_id(benchmark_data),
        },
        "prompt": prompt,
        "failure": {
            "signature": failure_signature(run),
            "summary": failure,
        },
        "goal": started_data.get("goal"),
        "recorded_configuration": {
            "provider": started_data.get("provider") or _request_configuration(run).get("provider"),
            "model": started_data.get("model") or _request_configuration(run).get("model"),
            "system_prompt_sha256": started_data.get("system_prompt_sha256"),
            "tool_config_sha256": started_data.get("tool_config_sha256"),
            "tools": started_data.get("tools", []),
        },
        "replay_boundaries": [
            {
                "request_id": record.get("data", {}).get("request_id"),
                "purpose": record.get("data", {}).get("purpose"),
                "context": record.get("data", {}).get("context"),
                "recorded_output": record.get("data", {}).get("output"),
                "recorded_stop_reason": record.get("data", {}).get("stop_reason"),
            }
            for record in _replayable_requests(run, benchmark_record is not None)
        ],
        "judge": _benchmark_judge(benchmark_data) if benchmark_record else None,
        "expectations": {
            "model_boundary_errors": 0,
            "forbidden_failure_signature": failure_signature(run),
            "note": (
                "Boundary replay evaluates model decisions without executing tools. Add workspace "
                "fixtures and deterministic checks before treating this as full task correctness."
            ),
        },
    }
    if output_path is not None:
        target = Path(output_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return case


def migrate_legacy_traces(root: str | Path) -> dict[str, int]:
    root_path = Path(root).resolve()
    session_dirs = {
        path.parent for name in ("tool-calls.jsonl", "model-requests.jsonl") for path in root_path.rglob(name)
    }
    migrated = 0
    skipped = 0
    for session_dir in sorted(session_dirs):
        if (session_dir / "trace.jsonl").exists():
            skipped += 1
            continue
        _migrate_legacy_session(session_dir)
        migrated += 1
    return {"migrated": migrated, "skipped": skipped}


def generate_dashboard(
    root: str | Path,
    output_dir: str | Path,
    baseline_path: str | Path | None = None,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    trace_selections: dict[Path, set[str] | None] = {
        path.resolve(): None for path in root_path.rglob("trace.jsonl")
    }
    for case_path in root_path.rglob("eval-case.json"):
        case = _read_json(case_path)
        source = case.get("source") if isinstance(case, dict) else None
        session_dir = source.get("session_dir") if isinstance(source, dict) else None
        if isinstance(session_dir, str):
            source_trace = (Path(session_dir) / "trace.jsonl").resolve()
            if source_trace.is_file():
                run_id = source.get("run_id") if isinstance(source, dict) else None
                if source_trace not in trace_selections:
                    trace_selections[source_trace] = {run_id} if isinstance(run_id, str) else None
                elif trace_selections[source_trace] is not None and isinstance(run_id, str):
                    trace_selections[source_trace].add(run_id)
    runs = [
        run
        for trace_path, selected_run_ids in sorted(trace_selections.items())
        for run in group_trace_runs(read_trace_records(trace_path))
        if selected_run_ids is None or run.get("run_id") in selected_run_ids
    ]
    eval_results = [
        value
        for result_path in root_path.rglob("eval-results.jsonl")
        for value in _read_jsonl(result_path)
        if isinstance(value, dict) and isinstance(value.get("passed"), bool)
    ]
    baseline = _read_json(Path(baseline_path)) if baseline_path else None
    summary = _build_dashboard(len(trace_selections), runs, eval_results, baseline)
    target = Path(output_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    (target / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (target / "report.md").write_text(_render_markdown(summary), encoding="utf-8")
    (target / "dashboard.html").write_text(_render_html(summary), encoding="utf-8")
    return summary


def failure_signature(run: dict[str, Any]) -> str:
    benchmark = _latest_benchmark_case(run, failure_only=True)
    if benchmark:
        data = benchmark.get("data") or {}
        scope = str(
            data.get("family")
            or data.get("category")
            or data.get("question_type")
            or data.get("source")
            or "benchmark"
        )
        reason = str(data.get("failure_reason") or "")
        if reason:
            reason = reason.split(":", 1)[0]
        elif isinstance(data.get("judge"), dict) and data["judge"].get("label") is False:
            reason = "judge_rejected"
        elif data.get("correct") is False:
            reason = "answer_mismatch"
        elif data.get("substring_exact_match") is False or data.get("exact_match") is False:
            reason = "answer_mismatch"
        else:
            reason = _error_code(data.get("error")) or "failed"
        return f"benchmark:{scope}:{reason}"
    for request in run.get("model_requests", []):
        data = request.get("data") or {}
        if data.get("status") == "error":
            return f"model:{_error_code(data.get('error')) or data.get('stop_reason') or 'error'}"
    for call in run.get("tool_calls", []):
        data = call.get("data") or {}
        if data.get("status") == "error":
            detail = _error_code(data.get("error")) or _normalized_error(data.get("error")) or "error"
            return f"tool:{data.get('tool_name', 'unknown')}:{detail}"
    completed = (run.get("completed") or {}).get("data") or {}
    goal = _latest_goal(run) or completed.get("goal")
    if isinstance(goal, dict) and goal.get("status") == "failed":
        return f"goal:{_normalized_error(goal.get('final_result')) or 'failed'}"
    return f"run:{completed.get('stop_reason') or 'incomplete'}"


def describe_failure(run: dict[str, Any]) -> str:
    benchmark = _latest_benchmark_case(run, failure_only=True)
    if benchmark:
        data = benchmark.get("data") or {}
        reason = data.get("failure_reason") or data.get("error")
        if isinstance(reason, str) and reason.strip():
            return _truncate(reason.strip(), 500)
        expected = data.get("answers") if isinstance(data.get("answers"), list) else data.get("answer")
        if expected is None:
            expected = data.get("expected") or data.get("answer_gold")
        output = (
            data.get("output")
            or data.get("response_raw")
            or data.get("response_parsed_boxed")
            or ""
        )
        return _truncate(
            f"expected={expected!r}; output={str(output)!r}",
            500,
        )
    completed = (run.get("completed") or {}).get("data") or {}
    candidates = [completed.get("error")]
    candidates.extend(
        (request.get("data") or {}).get("error") for request in run.get("model_requests", [])
    )
    candidates.extend((call.get("data") or {}).get("error") for call in run.get("tool_calls", []))
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return _truncate(value.strip(), 500)
    return _truncate(f"run ended with {completed.get('stop_reason') or 'an incomplete trace'}", 500)


def _build_dashboard(
    sessions: int,
    runs: list[dict[str, Any]],
    eval_results: list[dict[str, Any]],
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    completed = [run for run in runs if run.get("completed") or run.get("benchmark_cases")]
    success = [run for run in completed if _run_status(run) == "success"]
    cancelled = [
        run
        for run in completed
        if _run_status(run) in {"cancelled", "aborted"}
    ]
    operational = [run for run in completed if run not in cancelled]
    usage = [_run_usage(run) for run in completed]
    durations = [_run_duration_ms(run) for run in completed]
    tools = [call for run in runs for call in run.get("tool_calls", [])]
    models = [request for run in runs for request in run.get("model_requests", [])]
    model_transports = [item for run in runs for item in run.get("model_transports", [])]
    all_compactions = [item for run in runs for item in run.get("compactions", [])]
    compactions = [item for item in all_compactions if item.get("type") == "compaction.completed"]
    model_groups: dict[str, dict[str, Any]] = {}
    for item in models:
        data = item.get("data") or {}
        key = f"{data.get('provider', 'unknown')}/{data.get('model', 'unknown')}"
        group = model_groups.setdefault(
            key,
            {
                "provider_model": key,
                "requests": 0,
                "errors": 0,
                "tokens": 0,
                "cost_usd": 0.0,
                "total_duration_ms": 0.0,
                "purposes": {},
            },
        )
        group["requests"] += 1
        group["errors"] += data.get("status") == "error"
        group["tokens"] += int((data.get("usage") or {}).get("total_tokens", 0))
        group["cost_usd"] += float((data.get("usage") or {}).get("cost_usd", 0))
        group["total_duration_ms"] += float(data.get("duration_ms", 0))
        purpose = str(data.get("purpose") or "agent")
        group["purposes"][purpose] = group["purposes"].get(purpose, 0) + 1
    for group in model_groups.values():
        group["average_duration_ms"] = group.pop("total_duration_ms") / group["requests"]
        group["cost_usd"] = round(group["cost_usd"], 8)
    tool_groups: dict[str, dict[str, Any]] = {}
    for item in tools:
        data = item.get("data") or {}
        name = str(data.get("tool_name") or "unknown")
        group = tool_groups.setdefault(
            name,
            {
                "tool_name": name,
                "calls": 0,
                "errors": 0,
                "cancelled": 0,
                "total_duration_ms": 0.0,
            },
        )
        group["calls"] += 1
        group["errors"] += data.get("status") == "error"
        group["cancelled"] += data.get("status") == "cancelled"
        group["total_duration_ms"] += float(data.get("duration_ms", 0))
    for group in tool_groups.values():
        group["error_rate"] = _ratio(group["errors"], group["calls"])
        group["average_duration_ms"] = group.pop("total_duration_ms") / group["calls"]
    goals_by_identity: dict[str, dict[str, Any]] = {}
    for run in completed:
        completed_data = (run.get("completed") or {}).get("data") or {}
        goal = _latest_goal(run) or completed_data.get("goal")
        if not isinstance(goal, dict):
            continue
        identity = f"{run['trace_id']}:{goal.get('created_at') or goal.get('goal') or run['run_id']}"
        previous = goals_by_identity.get(identity)
        if previous is None or str(goal.get("updated_at", "")) >= str(previous.get("updated_at", "")):
            goals_by_identity[identity] = goal
    goal_runs = list(goals_by_identity.values())
    completed_goals = [goal for goal in goal_runs if goal.get("status") == "complete"]
    clusters: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"signature": "", "count": 0, "run_ids": [], "examples": []}
    )
    for run in completed:
        if run in success or run in cancelled:
            continue
        signature = failure_signature(run)
        cluster = clusters[signature]
        cluster["signature"] = signature
        cluster["count"] += 1
        cluster["run_ids"].append(run["run_id"])
        example = describe_failure(run)
        if example not in cluster["examples"] and len(cluster["examples"]) < 3:
            cluster["examples"].append(example)
    summary = {
        "version": 1,
        "created_at": _utc_now(),
        "sessions": sessions,
        "runs": len(completed),
        "successful_runs": len(success),
        "cancelled_runs": len(cancelled),
        "failure_rate": _ratio(len(operational) - len(success), len(operational)),
        "eval_attempts": len(eval_results),
        "eval_passed": sum(bool(item["passed"]) for item in eval_results),
        "eval_pass_rate": _ratio(sum(bool(item["passed"]) for item in eval_results), len(eval_results)),
        "goal_runs": len(goal_runs),
        "goal_completed": len(completed_goals),
        "goal_completion_rate": _ratio(len(completed_goals), len(goal_runs)),
        "average_cost_usd": _average([float(item.get("cost_usd", 0)) for item in usage]),
        "total_cost_usd": sum(float(item.get("cost_usd", 0)) for item in usage),
        "average_tokens": _average([float(item.get("total_tokens", 0)) for item in usage]),
        "total_tokens": sum(int(item.get("total_tokens", 0)) for item in usage),
        "average_duration_ms": _average(durations),
        "duration_p95_ms": _percentile(durations, 0.95),
        "model_requests": len(models),
        "model_errors": sum((item.get("data") or {}).get("status") == "error" for item in models),
        "model_retries": sum(
            (item.get("data") or {}).get("phase") == "retry_scheduled"
            for item in model_transports
        ),
        "model_fallbacks": sum(
            (item.get("data") or {}).get("phase") == "fallback_selected"
            for item in model_transports
        ),
        "model_breakdown": sorted(model_groups.values(), key=lambda item: item["provider_model"]),
        "tool_calls": len(tools),
        "tool_errors": sum((item.get("data") or {}).get("status") == "error" for item in tools),
        "tool_cancelled": sum(
            (item.get("data") or {}).get("status") == "cancelled" for item in tools
        ),
        "tool_error_rate": _ratio(
            sum((item.get("data") or {}).get("status") == "error" for item in tools),
            len(tools),
        ),
        "tool_breakdown": sorted(tool_groups.values(), key=lambda item: item["tool_name"]),
        "approval_decisions": sum(len(run.get("approvals", [])) for run in runs),
        "compaction_attempts": sum(item.get("type") == "compaction.started" for item in all_compactions),
        "compactions": len(compactions),
        "compaction_failures": sum(item.get("type") == "compaction.failed" for item in all_compactions),
        "compaction_aborted": sum(item.get("type") == "compaction.aborted" for item in all_compactions),
        "compaction_deferred": sum(item.get("type") == "compaction.deferred" for item in all_compactions),
        "compaction_strategies": {
            strategy: sum((item.get("data") or {}).get("strategy") == strategy for item in compactions)
            for strategy in sorted(
                {
                    str((item.get("data") or {}).get("strategy"))
                    for item in compactions
                    if (item.get("data") or {}).get("strategy")
                }
            )
        },
        "compaction_tokens_saved": sum(
            max(0, int(item["data"].get("tokens_before", 0)) - int(item["data"].get("tokens_after", 0)))
            for item in compactions
        ),
        "failure_clusters": sorted(clusters.values(), key=lambda item: (-item["count"], item["signature"])),
        "regression_alerts": [],
    }
    if isinstance(baseline, dict):
        summary["regression_alerts"] = _compare_dashboard(summary, baseline)
    return summary


def _compare_dashboard(current: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    alerts: list[str] = []
    if float(current["failure_rate"]) - float(baseline.get("failure_rate", 0)) >= 0.05:
        alerts.append("Failure rate increased by at least 5 percentage points.")
    if current["eval_attempts"] and baseline.get("eval_attempts") and (
        float(baseline.get("eval_pass_rate", 0)) - float(current["eval_pass_rate"]) >= 0.05
    ):
        alerts.append("Eval pass rate decreased by at least 5 percentage points.")
    if _relative_increase(current["duration_p95_ms"], baseline.get("duration_p95_ms", 0)) >= 0.2:
        alerts.append("p95 latency increased by at least 20%.")
    if _relative_increase(current["average_cost_usd"], baseline.get("average_cost_usd", 0)) >= 0.2:
        alerts.append("Average cost increased by at least 20%.")
    if float(current["tool_error_rate"]) - float(baseline.get("tool_error_rate", 0)) >= 0.05:
        alerts.append("Tool error rate increased by at least 5 percentage points.")
    if current["goal_runs"] and baseline.get("goal_runs") and (
        float(baseline.get("goal_completion_rate", 0)) - float(current["goal_completion_rate"]) >= 0.05
    ):
        alerts.append("Goal completion rate decreased by at least 5 percentage points.")
    return alerts


def _migrate_legacy_session(session_dir: Path) -> None:
    tools = _read_jsonl(session_dir / "tool-calls.jsonl")
    models = _read_jsonl(session_dir / "model-requests.jsonl")
    prompt_context = _read_json(session_dir / "last_prompt.jsonl") or {}
    identity = next((item for item in [*tools, *models] if isinstance(item, dict)), {})
    channel = str(identity.get("channelId") or session_dir.parent.name)
    conversation = str(identity.get("conversationId") or session_dir.name)
    recorder = TraceRecorder(session_dir, channel, conversation)
    run_id = f"legacy-{hashlib.sha256(str(session_dir).encode()).hexdigest()[:16]}"
    request = str(prompt_context.get("newUserMessage") or "Legacy session replay")
    latest_model = next((item for item in reversed(models) if isinstance(item, dict)), {})
    context = {
        "profile": {"model_id": latest_model.get("model", "unknown")},
        "messages": prompt_context.get("messages", []),
        "tools": prompt_context.get("tools", []),
        "temperature": None,
        "metadata": {"purpose": "legacy"},
    }
    recorder.record(
        "run.started",
        {
            "request": request,
            "provider": latest_model.get("provider", "unknown"),
            "model": latest_model.get("model", "unknown"),
            "system_prompt_sha256": hashlib.sha256(str(prompt_context.get("systemPrompt", "")).encode()).hexdigest(),
            "tool_config_sha256": "legacy",
            "tools": [item.get("name") for item in prompt_context.get("tools", []) if isinstance(item, dict)],
            "goal": _read_json(session_dir / "goal.json"),
            "migrated_from": ["tool-calls.jsonl", "model-requests.jsonl"],
        },
        run_id=run_id,
    )
    usage = zero_usage()
    for item in models:
        if not isinstance(item, dict):
            continue
        tokens = item.get("tokens") if isinstance(item.get("tokens"), dict) else {}
        event_usage = {
            "input_tokens": int(tokens.get("input", 0)),
            "output_tokens": int(tokens.get("output", 0)),
            "cached_tokens": int(tokens.get("cacheRead", 0)),
            "total_tokens": int(tokens.get("total", 0)),
            "cost_usd": 0.0,
        }
        usage = add_usage(usage, event_usage)
        recorder.record(
            "model.request",
            {
                "request_id": item.get("requestId") or "legacy",
                "purpose": "legacy",
                "status": "error" if item.get("error") or item.get("stopReason") == "error" else "success",
                "provider": item.get("provider", "unknown"),
                "model": item.get("model", "unknown"),
                "started_at": item.get("startedAt"),
                "completed_at": item.get("completedAt"),
                "duration_ms": item.get("durationMs", 0),
                "stop_reason": item.get("stopReason", "legacy"),
                "error": item.get("error"),
                "usage": event_usage,
                "context": context,
                "output": None,
            },
            run_id=run_id,
            timestamp=item.get("startedAt"),
        )
    for item in tools:
        if not isinstance(item, dict):
            continue
        if item.get("recordType") == "approval":
            recorder.record_approval(
                run_id=run_id,
                tool_name=str(item.get("toolName", "unknown")),
                risk=str(item.get("risk", "unknown")),
                reason=str(item.get("reason", "")),
                preview=str(item.get("preview", "")),
                decision=str(item.get("decision", "unknown")),
            )
            continue
        recorder.record(
            "tool.call",
            {
                "tool_call_id": item.get("toolCallId", "legacy"),
                "tool_name": item.get("toolName", "unknown"),
                "status": item.get("status", "error"),
                "started_at": item.get("startedAt"),
                "completed_at": item.get("completedAt"),
                "duration_ms": item.get("durationMs", 0),
                "arguments": item.get("args", {}),
                "result": item.get("result"),
                "error": item.get("error"),
            },
            run_id=run_id,
            timestamp=item.get("startedAt"),
        )
    failed = any(isinstance(item, dict) and item.get("error") for item in models)
    recorder.record(
        "run.completed",
        {
            "status": "error" if failed else "success",
            "stop_reason": "legacy",
            "error": next((item.get("error") for item in models if isinstance(item, dict) and item.get("error")), None),
            "final_text": "",
            "duration_ms": sum(float(item.get("durationMs", 0)) for item in models if isinstance(item, dict)),
            "usage": usage,
            "process": {"model_requests": len(models), "tool_calls": len(tools)},
            "goal": _read_json(session_dir / "goal.json"),
        },
        run_id=run_id,
    )


def _select_run(runs: list[dict[str, Any]], run_id: str | None, prefer_failure: bool) -> dict[str, Any]:
    if run_id:
        selected = next((run for run in runs if run["run_id"] == run_id), None)
        if selected is None:
            raise ValueError(f"Trace run not found: {run_id}")
        return selected
    candidates = [
        run
        for run in runs
        if not prefer_failure
        or (run.get("completed") or {}).get("data", {}).get("status") == "error"
        or _latest_benchmark_case(run, failure_only=True) is not None
        or any(
            (item.get("data") or {}).get("status") == "error"
            for item in run.get("tool_calls", [])
        )
    ]
    selected = (candidates or runs)[-1] if (candidates or runs) else None
    if selected is None:
        raise ValueError("No trace runs found")
    return selected


def _benchmark_case_id(data: dict[str, Any]) -> str | None:
    for key in ("case_id", "question_id", "task_id"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _benchmark_case_failed(data: dict[str, Any]) -> bool:
    for key in ("success", "correct"):
        value = data.get(key)
        if isinstance(value, bool):
            return not value
    judge = data.get("judge")
    if isinstance(judge, dict) and isinstance(judge.get("label"), bool):
        return not judge["label"]
    for key in ("substring_exact_match", "exact_match"):
        value = data.get(key)
        if isinstance(value, bool):
            return not value
    return bool(data.get("error") or data.get("failure_reason"))


def _latest_benchmark_case(
    run: dict[str, Any],
    *,
    failure_only: bool = False,
) -> dict[str, Any] | None:
    for record in reversed(run.get("benchmark_cases", [])):
        data = record.get("data") or {}
        if not failure_only or _benchmark_case_failed(data):
            return record
    return None


def _run_status(run: dict[str, Any]) -> str:
    benchmark = _latest_benchmark_case(run)
    if benchmark:
        return "error" if _benchmark_case_failed(benchmark.get("data") or {}) else "success"
    return str(((run.get("completed") or {}).get("data") or {}).get("status") or "incomplete")


def _run_usage(run: dict[str, Any]) -> dict[str, Any]:
    completed = (run.get("completed") or {}).get("data") or {}
    if isinstance(completed.get("usage"), dict):
        return completed["usage"]
    benchmark = (_latest_benchmark_case(run) or {}).get("data") or {}
    trace_metrics = (
        benchmark.get("trace_metrics")
        if isinstance(benchmark.get("trace_metrics"), dict)
        else {}
    )
    if isinstance(trace_metrics.get("usage"), dict):
        return trace_metrics["usage"]
    if isinstance(benchmark.get("usage"), dict):
        raw = benchmark["usage"]
        input_tokens = int(raw.get("input_tokens", 0))
        output_tokens = int(raw.get("output_tokens", 0))
        cached_tokens = int(raw.get("cached_tokens", 0))
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "total_tokens": int(
                raw.get("total_tokens", input_tokens + output_tokens)
            ),
            "cost_usd": float(raw.get("cost_usd", 0)),
        }
    metrics = benchmark.get("metrics") if isinstance(benchmark.get("metrics"), dict) else {}
    return metrics.get("usage") if isinstance(metrics.get("usage"), dict) else zero_usage()


def _run_duration_ms(run: dict[str, Any]) -> float:
    completed = (run.get("completed") or {}).get("data") or {}
    if isinstance(completed.get("duration_ms"), (int, float)):
        return float(completed["duration_ms"])
    benchmark = (_latest_benchmark_case(run) or {}).get("data") or {}
    if isinstance(benchmark.get("elapsed_seconds"), (int, float)):
        return float(benchmark["elapsed_seconds"]) * 1_000
    return sum(
        float((record.get("data") or {}).get("duration_ms", 0))
        for record in run.get("model_requests", [])
    )


def _request_configuration(run: dict[str, Any]) -> dict[str, Any]:
    requests = _replayable_requests(run, benchmark=True) or run.get("model_requests", [])
    return (requests[-1].get("data") or {}) if requests else {}


def _replayable_requests(run: dict[str, Any], benchmark: bool) -> list[dict[str, Any]]:
    requests = list(run.get("model_requests", []))
    if not benchmark:
        return requests
    selected = []
    for record in requests:
        purpose = str((record.get("data") or {}).get("purpose") or "agent").lower()
        if (
            any(marker in purpose for marker in ("judge", "grader", "evaluator"))
            or purpose.endswith("_checker")
            or purpose in {"compaction", "memory_fact_extract"}
        ):
            continue
        selected.append(record)
    return selected or requests


def _benchmark_judge(data: dict[str, Any]) -> dict[str, Any] | None:
    spec = data.get("judge_spec")
    if isinstance(spec, dict) and isinstance(spec.get("regexes"), list):
        return {"type": "regex_all", "patterns": [str(item) for item in spec["regexes"]]}
    judge = data.get("judge")
    if (
        isinstance(judge, dict)
        and isinstance(judge.get("label"), bool)
        and isinstance(data.get("question"), str)
        and isinstance(data.get("answer"), str)
    ):
        return {
            "type": "llm_rubric",
            "question": data["question"],
            "rubric": data["answer"],
        }
    answers = data.get("answers")
    if isinstance(answers, list) and answers:
        return {"type": "normalized_contains_any", "expected": [str(item) for item in answers]}
    expected = data.get("expected")
    if isinstance(expected, list) and expected:
        return {"type": "normalized_contains_any", "expected": [str(item) for item in expected]}
    if isinstance(expected, str) and expected:
        return {"type": "normalized_contains_any", "expected": [expected]}
    answer_gold = data.get("answer_gold")
    eval_function = data.get("eval_function")
    if isinstance(answer_gold, str) and answer_gold.strip() and isinstance(eval_function, str):
        eval_name, _, raw_options = eval_function.partition("|")
        if eval_name in {"llm_abstention_checker", "llm_gotchas_checker"}:
            return {
                "type": "llm_rubric",
                "question": str(data.get("question") or ""),
                "rubric": answer_gold,
            }
        if eval_name in {"mc_choice_match", "mc_choice_set_match"}:
            return {
                "type": "normalized_exact",
                "expected": answer_gold,
            }
        if eval_name in {"norm_phrase_set_match", "norm_phrase_set_match_ordered"}:
            separators = ",;"
            for option in raw_options.split("|"):
                name, separator, value = option.partition("=")
                if separator and name.strip() == "separators":
                    separators = value
                    break
            phrases = [
                phrase.strip()
                for phrase in re.split(
                    "|".join(re.escape(value) for value in separators),
                    answer_gold,
                )
                if phrase.strip()
            ]
            return {
                "type": "normalized_phrase_set",
                "expected": phrases,
                "ordered": eval_name.endswith("_ordered"),
            }
    return None


def _latest_goal(run: dict[str, Any]) -> dict[str, Any] | None:
    for snapshot in reversed(run.get("goals", [])):
        goal = (snapshot.get("data") or {}).get("goal")
        if isinstance(goal, dict):
            return goal
    return None


def _render_markdown(summary: dict[str, Any]) -> str:
    alerts = "\n".join(f"- {item}" for item in summary["regression_alerts"]) or "- None"
    cluster_lines: list[str] = []
    for item in summary["failure_clusters"]:
        signature = str(item["signature"]).replace("|", "\\|")
        example = str(item["examples"][0] if item["examples"] else "").replace("|", "\\|")
        cluster_lines.append(f"| {signature} | {item['count']} | {example} |")
    clusters = "\n".join(cluster_lines) or "| (none) | 0 | |"
    return f"""# MiniClaw Trace quality and cost dashboard

Generated: {summary['created_at']}

| Metric | Value |
| --- | ---: |
| Runs | {summary['runs']} |
| Cancelled runs | {summary['cancelled_runs']} |
| Operational success | {_percent(1 - summary['failure_rate'])} |
| Eval pass rate | {_percent(summary['eval_pass_rate']) if summary['eval_attempts'] else 'n/a'} |
| Goal completion | {_percent(summary['goal_completion_rate']) if summary['goal_runs'] else 'n/a'} |
| Total tokens | {summary['total_tokens']} |
| Average cost | ${summary['average_cost_usd']:.6f} |
| Total cost | ${summary['total_cost_usd']:.6f} |
| Average latency | {summary['average_duration_ms']:.0f}ms |
| p95 latency | {summary['duration_p95_ms']:.0f}ms |
| Model retries | {summary['model_retries']} |
| Model fallbacks | {summary['model_fallbacks']} |
| Tool error rate | {_percent(summary['tool_error_rate'])} |
| Cancelled tool calls | {summary['tool_cancelled']} |
| Compactions | {summary['compactions']} |
| Compaction failures | {summary['compaction_failures']} |
| Compaction tokens saved | {summary['compaction_tokens_saved']} |

## Regression alerts

{alerts}

## Model breakdown

| Provider / model | Requests | Errors | Tokens | Cost | Average latency |
| --- | ---: | ---: | ---: | ---: | ---: |
{_model_markdown_rows(summary)}

## Tool breakdown

| Tool | Calls | Errors | Cancelled | Error rate | Average latency |
| --- | ---: | ---: | ---: | ---: | ---: |
{_tool_markdown_rows(summary)}

## Failure clusters

| Signature | Count | Example |
| --- | ---: | --- |
{clusters}
"""


def _render_html(summary: dict[str, Any]) -> str:
    cards = [
        ("Operational success", _percent(1 - summary["failure_rate"])),
        ("Eval quality", _percent(summary["eval_pass_rate"]) if summary["eval_attempts"] else "n/a"),
        ("Cancelled runs", str(summary["cancelled_runs"])),
        ("Goal completion", _percent(summary["goal_completion_rate"]) if summary["goal_runs"] else "n/a"),
        ("Average cost", f"${summary['average_cost_usd']:.6f}"),
        ("p95 latency", f"{summary['duration_p95_ms']:.0f}ms"),
        ("Model retries", str(summary["model_retries"])),
        ("Model fallbacks", str(summary["model_fallbacks"])),
        ("Tokens saved", str(summary["compaction_tokens_saved"])),
    ]
    card_html = "".join(
        f'<div class="card"><div>{html.escape(label)}</div><strong>{html.escape(value)}</strong></div>'
        for label, value in cards
    )
    rows = "".join(
        f"<tr><td>{html.escape(item['signature'])}</td><td>{item['count']}</td><td>{html.escape(item['examples'][0] if item['examples'] else '')}</td></tr>"
        for item in summary["failure_clusters"]
    )
    alerts = "".join(f"<li>{html.escape(item)}</li>" for item in summary["regression_alerts"]) or "<li>None</li>"
    model_rows = "".join(
        f"<tr><td>{html.escape(item['provider_model'])}</td><td>{item['requests']}</td><td>{item['errors']}</td><td>{item['tokens']}</td><td>${item['cost_usd']:.6f}</td><td>{item['average_duration_ms']:.0f}ms</td></tr>"
        for item in summary["model_breakdown"]
    )
    tool_rows = "".join(
        f"<tr><td>{html.escape(item['tool_name'])}</td><td>{item['calls']}</td><td>{item['errors']}</td><td>{item['cancelled']}</td><td>{_percent(item['error_rate'])}</td><td>{item['average_duration_ms']:.0f}ms</td></tr>"
        for item in summary["tool_breakdown"]
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>MiniClaw Trace Dashboard</title>
<style>body{{font:14px system-ui;margin:32px;color:#172033}}main{{max-width:1100px;margin:auto}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}.card{{border:1px solid #d7dce5;border-radius:10px;padding:16px}}strong{{display:block;font-size:24px;margin-top:8px}}table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid #e5e7eb;padding:8px;text-align:left}}</style></head>
<body><main><h1>MiniClaw Trace quality and cost</h1><div class="grid">{card_html}</div><h2>Regression alerts</h2><ul>{alerts}</ul><h2>Model breakdown</h2><table><thead><tr><th>Provider / model</th><th>Requests</th><th>Errors</th><th>Tokens</th><th>Cost</th><th>Average latency</th></tr></thead><tbody>{model_rows}</tbody></table><h2>Tool breakdown</h2><table><thead><tr><th>Tool</th><th>Calls</th><th>Errors</th><th>Cancelled</th><th>Error rate</th><th>Average latency</th></tr></thead><tbody>{tool_rows}</tbody></table><h2>Failure clusters</h2><table><thead><tr><th>Signature</th><th>Count</th><th>Example</th></tr></thead><tbody>{rows}</tbody></table></main></body></html>"""


def _model_markdown_rows(summary: dict[str, Any]) -> str:
    rows = [
        f"| {item['provider_model']} | {item['requests']} | {item['errors']} | {item['tokens']} | ${item['cost_usd']:.6f} | {item['average_duration_ms']:.0f}ms |"
        for item in summary["model_breakdown"]
    ]
    return "\n".join(rows) or "| (none) | 0 | 0 | 0 | $0 | 0ms |"


def _tool_markdown_rows(summary: dict[str, Any]) -> str:
    rows = [
        f"| {item['tool_name']} | {item['calls']} | {item['errors']} | {item['cancelled']} | {_percent(item['error_rate'])} | {item['average_duration_ms']:.0f}ms |"
        for item in summary["tool_breakdown"]
    ]
    return "\n".join(rows) or "| (none) | 0 | 0 | 0 | 0% | 0ms |"


def _read_jsonl(path: Path) -> list[Any]:
    if not path.exists():
        return []
    values: list[Any] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            values.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return values


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _error_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.match(r"^\[([A-Z0-9_]+)\]", value)
    return match.group(1) if match else None


def _normalized_error(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    first = value.strip().splitlines()[0]
    first = re.sub(r"\b\d+\b", "#", first)
    first = re.sub(r"[A-Fa-f0-9]{12,}", "<id>", first)
    return _slugify(first)[:80] or None


def _slugify(value: str) -> str:
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", value.lower()))


def _truncate(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else f"{value[:maximum]}…"


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _ratio(left: int, right: int) -> float:
    return left / right if right else 0.0


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(math.ceil(len(ordered) * quantile)) - 1))
    return ordered[index]


def _relative_increase(current: Any, baseline: Any) -> float:
    current_value = float(current or 0)
    baseline_value = float(baseline or 0)
    if baseline_value <= 0:
        return float("inf") if current_value > baseline_value else 0.0
    return (current_value - baseline_value) / baseline_value


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
