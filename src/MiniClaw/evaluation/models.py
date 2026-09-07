from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal


EVAL_DIMENSIONS = ("outcome", "process", "efficiency", "safety", "reliability")
EVAL_CHECK_TYPES = {
    "file_exists",
    "file_absent",
    "directory_exists",
    "file_contains",
    "file_not_contains",
    "file_equals",
    "file_regex",
    "case_file_absent",
    "command",
    "oracle",
    "final_contains",
    "final_not_contains",
    "final_equals",
    "final_normalized_equals",
    "final_regex",
    "final_json",
    "llm_rubric",
    "goal_status",
    "trace_event",
    "trace_sequence",
    "workspace_diff",
    "tool_write_paths",
    "metric",
}
EVAL_BUDGETS = {
    "max_total_tokens",
    "max_input_tokens",
    "max_output_tokens",
    "max_cost_usd",
    "max_model_requests",
    "max_agent_model_requests",
    "max_auxiliary_model_requests",
    "max_agent_tokens",
    "max_memory_tokens",
    "max_compaction_tokens",
    "max_model_errors",
    "max_model_retries",
    "max_model_fallbacks",
    "max_tool_calls",
    "max_tool_errors",
    "max_tool_cancelled",
    "max_tool_blocked",
    "max_goal_attempts",
    "max_ttft_p95_ms",
    "max_model_latency_p95_ms",
    "max_duration_seconds",
    "max_wall_seconds",
    "min_cache_ratio",
    "min_compactions",
    "min_compaction_tokens_saved",
    "min_live_tool_artifacts",
    "min_archived_tool_artifacts",
    "min_history_archives",
    "min_model_summary_compactions",
    "min_memory_injected_items",
    "min_instruction_sources",
}

EVAL_PHASE_CONTROLS = {
    "resume_goal",
    "cancel_after_seconds",
    "cancel_on_tool",
    "cancel_delay_seconds",
    "inject_http_statuses",
    "inject_network_disconnects",
    "inject_retry_after_seconds",
    "mock_success_text",
    "approval_response",
    "approval_delay_seconds",
}


@dataclass(slots=True, frozen=True)
class EvalPhase:
    id: str
    prompt: str
    control: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class EvalCheck:
    type: str
    dimension: str = "outcome"
    required: bool = True
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class EvalCase:
    id: str
    category: str
    fixture: str | None
    phases: tuple[EvalPhase, ...]
    checks: tuple[EvalCheck, ...]
    goal: dict[str, Any] | None = None
    environment: dict[str, str] = field(default_factory=dict)
    budgets: dict[str, float] = field(default_factory=dict)
    timeout_seconds: float = 600.0
    source: dict[str, Any] = field(default_factory=dict)
    repetitions: int = 1
    min_pass_rate: float = 1.0
    capabilities: tuple[str, ...] = ()
    session_mode: Literal["isolated", "shared"] = "isolated"
    fixture_root: Path = Path(".")


@dataclass(slots=True, frozen=True)
class EvalSuite:
    name: str
    version: int
    path: Path
    cases: tuple[EvalCase, ...]
    environment: dict[str, str] = field(default_factory=dict)
    required_dimensions: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    includes: tuple[Path, ...] = ()


def load_eval_suite(path: str | Path, *, _seen: frozenset[Path] = frozenset()) -> EvalSuite:
    suite_path = Path(path).resolve()
    if suite_path in _seen:
        raise ValueError(f"Eval suite include cycle: {suite_path}")
    payload = json.loads(suite_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Eval suite must be a version 1 JSON object")
    name = _required_string(payload, "name")
    includes = _parse_includes(payload.get("includes"), suite_path)
    inherited_cases: list[EvalCase] = []
    next_seen = _seen | {suite_path}
    for include in includes:
        included = load_eval_suite(include, _seen=next_seen)
        inherited_cases.extend(
            replace(
                case,
                environment={**included.environment, **case.environment},
            )
            for case in included.cases
        )
    raw_cases = payload.get("cases") or []
    if not isinstance(raw_cases, list) or (not raw_cases and not inherited_cases):
        raise ValueError("Eval suite requires cases or includes")
    local_cases = [
        _parse_case(value, index, suite_path.parent)
        for index, value in enumerate(raw_cases)
    ]
    cases = tuple([*inherited_cases, *local_cases])
    case_ids = [case.id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Eval case ids must be unique")
    coverage = payload.get("coverage") or {}
    if not isinstance(coverage, dict):
        raise ValueError("Eval suite coverage must be an object")
    required_dimensions = tuple(_string_list(coverage.get("dimensions"), "coverage dimensions"))
    unknown_dimensions = set(required_dimensions) - set(EVAL_DIMENSIONS)
    if unknown_dimensions:
        raise ValueError(
            "Eval suite coverage has unknown dimensions: " + ", ".join(sorted(unknown_dimensions))
        )
    required_capabilities = tuple(
        _string_list(coverage.get("capabilities"), "coverage capabilities")
    )
    return EvalSuite(
        name=name,
        version=1,
        path=suite_path,
        cases=cases,
        environment=_string_mapping(payload.get("environment"), "suite environment"),
        required_dimensions=required_dimensions,
        required_capabilities=required_capabilities,
        includes=includes,
    )


def _parse_case(value: Any, index: int, fixture_root: Path) -> EvalCase:
    if not isinstance(value, dict):
        raise ValueError(f"Eval case {index + 1} must be an object")
    case_id = _required_string(value, "id")
    category = _required_string(value, "category")
    raw_phases = value.get("phases")
    if raw_phases is None:
        prompt = _required_string(value, "prompt")
        phases = (EvalPhase("main", prompt),)
    else:
        if not isinstance(raw_phases, list) or not raw_phases:
            raise ValueError(f"Eval case {case_id} phases must be a non-empty array")
        phases = tuple(_parse_phase(item, phase_index, case_id) for phase_index, item in enumerate(raw_phases))
    phase_ids = [phase.id for phase in phases]
    if len(phase_ids) != len(set(phase_ids)):
        raise ValueError(f"Eval case {case_id} phase ids must be unique")
    raw_checks = value.get("checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise ValueError(f"Eval case {case_id} requires checks")
    checks = tuple(_parse_check(item, check_index, case_id) for check_index, item in enumerate(raw_checks))
    checks = tuple(
        replace(check, options={**check.options, "script": str((fixture_root / check.options["script"]).resolve())})
        if check.type == "oracle" else check for check in checks
    )
    fixture = value.get("fixture")
    if fixture is not None and (not isinstance(fixture, str) or not fixture.strip()):
        raise ValueError(f"Eval case {case_id} fixture must be a path string")
    goal = value.get("goal")
    if goal is not None:
        if not isinstance(goal, dict):
            raise ValueError(f"Eval case {case_id} goal must be an object")
        _required_string(goal, "description")
        criteria = goal.get("acceptance_criteria")
        if not isinstance(criteria, list) or not criteria or not all(
            isinstance(item, str) and item.strip() for item in criteria
        ):
            raise ValueError(f"Eval case {case_id} goal requires acceptance_criteria")
    budgets = value.get("budgets") or {}
    if not isinstance(budgets, dict):
        raise ValueError(f"Eval case {case_id} budgets must be an object")
    parsed_budgets: dict[str, float] = {}
    for key, raw in budgets.items():
        if key not in EVAL_BUDGETS:
            raise ValueError(f"Eval case {case_id} has unknown budget: {key}")
        if not isinstance(key, str) or not isinstance(raw, (int, float)) or isinstance(raw, bool) or raw < 0:
            raise ValueError(f"Eval case {case_id} has an invalid budget")
        parsed_budgets[key] = float(raw)
    timeout = value.get("timeout_seconds", 600)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError(f"Eval case {case_id} timeout_seconds must be positive")
    source = value.get("source") or {}
    if not isinstance(source, dict):
        raise ValueError(f"Eval case {case_id} source must be an object")
    repetitions = value.get("repetitions", 1)
    if (
        not isinstance(repetitions, int)
        or isinstance(repetitions, bool)
        or repetitions < 1
        or repetitions > 20
    ):
        raise ValueError(f"Eval case {case_id} repetitions must be an integer from 1 to 20")
    min_pass_rate = value.get("min_pass_rate", 1.0)
    if (
        not isinstance(min_pass_rate, (int, float))
        or isinstance(min_pass_rate, bool)
        or not 0 < float(min_pass_rate) <= 1
    ):
        raise ValueError(f"Eval case {case_id} min_pass_rate must be in (0, 1]")
    capabilities = tuple(_string_list(value.get("capabilities"), f"case {case_id} capabilities"))
    session_mode = str(value.get("session_mode") or "isolated").strip().casefold()
    if session_mode not in {"isolated", "shared"}:
        raise ValueError(f"Eval case {case_id} session_mode must be isolated or shared")
    return EvalCase(
        id=case_id,
        category=category,
        fixture=fixture.strip() if isinstance(fixture, str) else None,
        phases=phases,
        checks=checks,
        goal=dict(goal) if isinstance(goal, dict) else None,
        environment=_string_mapping(value.get("environment"), f"case {case_id} environment"),
        budgets=parsed_budgets,
        timeout_seconds=float(timeout),
        source=dict(source),
        repetitions=repetitions,
        min_pass_rate=float(min_pass_rate),
        capabilities=capabilities,
        session_mode=session_mode,  # type: ignore[arg-type]
        fixture_root=fixture_root.resolve(),
    )


def _parse_phase(value: Any, index: int, case_id: str) -> EvalPhase:
    if not isinstance(value, dict):
        raise ValueError(f"Eval case {case_id} phase {index + 1} must be an object")
    control = value.get("control") or {}
    if not isinstance(control, dict):
        raise ValueError(f"Eval case {case_id} phase control must be an object")
    unknown = set(control) - EVAL_PHASE_CONTROLS
    if unknown:
        raise ValueError(
            f"Eval case {case_id} phase has unknown controls: {', '.join(sorted(unknown))}"
        )
    _validate_phase_control(control, case_id)
    return EvalPhase(
        id=_required_string(value, "id"),
        prompt=_required_string(value, "prompt"),
        control=dict(control),
    )


def _parse_check(value: Any, index: int, case_id: str) -> EvalCheck:
    if not isinstance(value, dict):
        raise ValueError(f"Eval case {case_id} check {index + 1} must be an object")
    check_type = _required_string(value, "type")
    if check_type not in EVAL_CHECK_TYPES:
        raise ValueError(f"Eval case {case_id} has unknown check type: {check_type}")
    dimension = str(value.get("dimension") or "outcome").strip().casefold()
    if dimension not in EVAL_DIMENSIONS:
        raise ValueError(f"Eval case {case_id} check dimension is invalid")
    required = value.get("required", True)
    if not isinstance(required, bool):
        raise ValueError(f"Eval case {case_id} check required must be boolean")
    return EvalCheck(
        type=check_type,
        dimension=dimension,
        required=required,
        options={
            key: item
            for key, item in value.items()
            if key not in {"type", "dimension", "required"}
        },
    )


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return item.strip()


def _string_mapping(value: Any, label: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError(f"{label} must contain string keys and values")
    return dict(value)


def _string_list(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{label} must be a string array")
    return [item.strip() for item in value]


def _parse_includes(value: Any, suite_path: Path) -> tuple[Path, ...]:
    includes = _string_list(value, "Eval suite includes")
    resolved: list[Path] = []
    root = suite_path.parent.resolve()
    for item in includes:
        path = (root / item).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Eval suite include must stay beside the suite: {item}") from exc
        if not path.is_file():
            raise FileNotFoundError(f"Eval suite include not found: {path}")
        resolved.append(path)
    return tuple(resolved)


def _validate_phase_control(control: dict[str, Any], case_id: str) -> None:
    for name in ("resume_goal",):
        if name in control and not isinstance(control[name], bool):
            raise ValueError(f"Eval case {case_id} control {name} must be boolean")
    for name in (
        "cancel_after_seconds",
        "cancel_delay_seconds",
        "inject_retry_after_seconds",
        "approval_delay_seconds",
    ):
        if name in control and (
            not isinstance(control[name], (int, float))
            or isinstance(control[name], bool)
            or float(control[name]) < 0
        ):
            raise ValueError(f"Eval case {case_id} control {name} must be non-negative")
    if "cancel_on_tool" in control and (
        not isinstance(control["cancel_on_tool"], str) or not control["cancel_on_tool"].strip()
    ):
        raise ValueError(f"Eval case {case_id} control cancel_on_tool must be a tool name")
    if "inject_http_statuses" in control:
        statuses = control["inject_http_statuses"]
        if not isinstance(statuses, list) or not statuses or not all(
            isinstance(item, int) and not isinstance(item, bool) and 400 <= item <= 599
            for item in statuses
        ):
            raise ValueError(
                f"Eval case {case_id} control inject_http_statuses must contain HTTP 4xx/5xx integers"
            )
    if "inject_network_disconnects" in control and (
        not isinstance(control["inject_network_disconnects"], int)
        or isinstance(control["inject_network_disconnects"], bool)
        or control["inject_network_disconnects"] < 1
    ):
        raise ValueError(
            f"Eval case {case_id} control inject_network_disconnects must be a positive integer"
        )
    if "mock_success_text" in control and not isinstance(control["mock_success_text"], str):
        raise ValueError(f"Eval case {case_id} control mock_success_text must be a string")
    if "approval_response" in control and control["approval_response"] not in {
        "approve", "deny", "timeout"
    }:
        raise ValueError(
            f"Eval case {case_id} control approval_response must be approve, deny, or timeout"
        )
