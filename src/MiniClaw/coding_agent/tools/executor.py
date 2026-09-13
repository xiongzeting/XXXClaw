from __future__ import annotations

"""The single tool-call boundary.

The agent loop knows how to speak the model protocol. Concrete tools know how
to do one operation. This module is the small boundary between the two:
receive a call, prepare and validate it, authorize it, execute it, and return
exactly one bounded result.

Keeping that sequence in one place is intentional. It makes safety decisions
auditable and prevents a second dispatcher from growing a different error,
retry, or cancellation contract.
"""

import asyncio
import contextlib
import copy
import hashlib
import inspect
import json
import math
import re
import shlex
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from MiniClaw.cancellation import CancellationToken, ToolCancelledError
from MiniClaw.llm.types import ToolInvocation

from .base import Tool, ToolContext, ToolError, ToolResult, ToolSpec
from .truncate import DEFAULT_MAX_BYTES, truncate_head


ResultTransform = Callable[[ToolInvocation, ToolResult], ToolResult | Awaitable[ToolResult]]
Preflight = Callable[[ToolInvocation], ToolResult | None | Awaitable[ToolResult | None]]
ToolOutcome = Literal["rejected", "succeeded", "failed", "timed_out", "cancelled"]
TOOL_LIFECYCLE_PHASES = (
    "received",
    "validated",
    "preflighted",
    "started",
    "succeeded",
    "failed",
    "timed_out",
    "cancelled",
    "transformed",
    "delivered",
)


# Stable provider-visible ordering. It keeps the common prefix stable without
# exposing internal retry/side-effect metadata to the model.
_TOOL_ORDER = {
    "read": 0,
    "bash": 1,
    "edit": 2,
    "write": 3,
    "grep": 4,
    "search": 5,
    "ls": 6,
    "find": 7,
    "memory": 10,
    "goal": 11,
    "goal_complete": 12,
}
_READ_ONLY_TOOLS = frozenset({"read", "grep", "search", "ls", "find"})
# Last-resort bound for tools that do not implement their own output policy.
# Large normal results are handled by the existing Artifact transform first.
DEFAULT_MODEL_OUTPUT_MAX_CHARS = 128_000
DEFAULT_MODEL_OUTPUT_MAX_BYTES = DEFAULT_MAX_BYTES


@dataclass(slots=True)
class ToolExecutor:
    """The one lifecycle for every model-issued tool call.

    The public lifecycle is deliberately short:

    ``received → validated → preflighted → started → terminal → transformed → delivered``

    ``events`` uses the useful terminal names ``succeeded``, ``failed``,
    ``timed_out`` and ``cancelled``. A call is authorized only once; transient
    retries repeat the operation, never the approval prompt.
    """

    # The tool set is fixed when the executor is built.  There is deliberately
    # no runtime registration, injection, enable/disable, or role policy API.
    # Composition belongs at the product boundary; execution belongs here.
    tools: Iterable[Tool] | Mapping[str, Tool] = field(default_factory=tuple)
    timeout_seconds: float | None = None
    # Keep the tool-specific 50KB bounds as the primary safety boundary. This
    # larger final character cap only protects tools without their own bound
    # and no longer cuts normal bash/read/grep results at 32K characters.
    max_output_chars: int | None = DEFAULT_MODEL_OUTPUT_MAX_CHARS
    max_output_bytes: int = DEFAULT_MODEL_OUTPUT_MAX_BYTES
    result_transforms: list[ResultTransform] = field(default_factory=list)
    preflights: list[Preflight] = field(default_factory=list)
    max_retries: int = 3
    retry_base_seconds: float = 0.25
    retry_max_seconds: float = 8.0
    context: ToolContext = field(default_factory=ToolContext)
    verification_cache_path: str | Path | None = None
    _tool_map: dict[str, Tool] = field(default_factory=dict, init=False, repr=False)
    _request_tool_names: frozenset[str] | None = field(default=None, init=False, repr=False)
    _call_history: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _repeatable_results: dict[str, ToolResult] = field(default_factory=dict, init=False, repr=False)
    _mutation_epoch: int = field(default=0, init=False, repr=False)
    _verification_cache: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        raw_tools = self.tools.values() if isinstance(self.tools, Mapping) else self.tools
        fixed_tools = tuple(raw_tools)
        tool_map: dict[str, Tool] = {}
        for tool in fixed_tools:
            name = getattr(tool, "name", None)
            if not isinstance(name, str) or not name.strip():
                raise ValueError("tool name must be a non-empty string")
            normalized_name = name.strip()
            if normalized_name in tool_map:
                raise ValueError(f"duplicate tool name: {normalized_name}")
            tool_map[normalized_name] = tool
        self.tools = fixed_tools
        self._tool_map = tool_map
        if self.timeout_seconds is not None and (
            not math.isfinite(float(self.timeout_seconds))
            or float(self.timeout_seconds) <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        if self.max_output_chars is not None and int(self.max_output_chars) < 0:
            raise ValueError("max_output_chars must be non-negative")
        if int(self.max_output_bytes) < 0:
            raise ValueError("max_output_bytes must be non-negative")
        if int(self.max_retries) < 0:
            raise ValueError("max_retries must be non-negative")
        if float(self.retry_base_seconds) < 0 or float(self.retry_max_seconds) < 0:
            raise ValueError("retry delays must be non-negative")
        if self.max_output_chars is not None:
            self.max_output_chars = int(self.max_output_chars)
        self.max_output_bytes = int(self.max_output_bytes)
        self.max_retries = int(self.max_retries)
        self._load_verification_cache()

    def _load_verification_cache(self) -> None:
        if self.verification_cache_path is None:
            return
        path = Path(self.verification_cache_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self._mutation_epoch = max(0, int(payload.get("mutation_epoch") or 0))
            entries = payload.get("entries") or {}
            if isinstance(entries, dict):
                self._verification_cache = {
                    str(key): value for key, value in entries.items()
                    if isinstance(value, dict) and isinstance(value.get("content"), str)
                }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._mutation_epoch = 0
            self._verification_cache = {}

    def _save_verification_cache(self) -> None:
        if self.verification_cache_path is None:
            return
        path = Path(self.verification_cache_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps({
                "version": 1,
                "mutation_epoch": self._mutation_epoch,
                "entries": self._verification_cache,
            }, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            # Verification caching is an optimization; never fail the tool call.
            return

    def _workspace_fingerprint(self, command: str) -> str:
        root = Path(self.context.workspace)
        digest = hashlib.sha256()
        if not root.exists():
            return digest.hexdigest()
        try:
            candidates: set[Path] = set()
            for token in shlex.split(command, posix=False):
                token = token.strip("'\"()[]{};,:")
                if not token or token.startswith("-"):
                    continue
                candidate = (root / token).resolve()
                if candidate.is_file() and root in candidate.parents:
                    candidates.add(candidate)
            files = sorted(candidates)
            for path in files:
                relative = path.relative_to(root).as_posix()
                digest.update(relative.encode("utf-8", "replace"))
                try:
                    digest.update(hashlib.sha256(path.read_bytes()).digest())
                except OSError:
                    digest.update(b"<unreadable>")
        except OSError:
            digest.update(b"<unavailable>")
        return digest.hexdigest()

    def _verification_key(self, call: ToolInvocation, arguments: dict[str, Any]) -> str:
        payload = {
            "command": str(arguments.get("command") or ""),
            "mutation_epoch": self._mutation_epoch,
            "workspace_hash": self._workspace_fingerprint(str(arguments.get("command") or "")),
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    @property
    def call_history(self) -> list[dict[str, Any]]:
        """Return a copy so observability consumers cannot change execution state."""

        return copy.deepcopy(self._call_history)

    def set_request_tool_names(self, names: Iterable[str] | None) -> None:
        """Temporarily bind execution to definitions sent in the current request."""

        self._request_tool_names = (
            None if names is None else frozenset(str(name) for name in names)
        )

    def is_available(self, name: str) -> bool:
        """Return whether a fixed tool is callable in the current request."""

        return name in self._tool_map and (
            self._request_tool_names is None or name in self._request_tool_names
        )

    def is_parallel_safe(self, name: str) -> bool:
        """Whether a call may overlap with adjacent calls in one model turn.

        The conservative allowlist intentionally covers only read-only tools.
        Memory mutations, shell commands, writes, edits, approvals, and unknown
        extensions stay serial so ordering and shared-state guarantees remain
        deterministic.
        """

        return name in _READ_ONLY_TOOLS and self.is_available(name)

    def available_names(self) -> tuple[str, ...]:
        names = [
            name
            for name in self._tool_map
            if self._request_tool_names is None or name in self._request_tool_names
        ]
        return tuple(sorted(names, key=_tool_sort_key))

    def definitions(self) -> list[dict[str, Any]]:
        """Return only the compact contract the model needs.

        Side-effect, retry, and capability metadata is runtime policy, not model
        input. Keeping it out of every request saves prefix tokens and avoids
        treating implementation hints as authorization.
        """

        return [
            {
                "name": name,
                "description": self._tool_map[name].description,
                "parameters": copy.deepcopy(self._tool_map[name].input_schema),
            }
            for name in self.available_names()
        ]

    def spec(self, name: str) -> ToolSpec:
        tool = self._tool_map[name]
        declared = getattr(tool, "tool_spec", None)
        if isinstance(declared, ToolSpec):
            return declared
        return ToolSpec(
            name=name,
            idempotent=bool(getattr(tool, "idempotent", False))
            or name in _READ_ONLY_TOOLS,
            side_effect="none" if name in _READ_ONLY_TOOLS else "unknown",
        )

    async def execute(
        self,
        call: ToolInvocation,
        cancellation_token: CancellationToken | None = None,
    ) -> ToolResult:
        """Execute one logical call and deliver one normalized result."""

        started_at = time.perf_counter()
        record = _new_record(call)
        _record_event(record, "received")

        tool = self._tool_map.get(call.name)
        if tool is None:
            result = _error_result(
                f"unknown tool: {call.name}",
                ToolError(
                    "UNKNOWN_TOOL",
                    "lookup",
                    f"tool is not part of this executor's fixed tool set: {call.name}",
                    not_started=True,
                ),
            )
            _record_event(record, "failed", error=result.details["error"])
            return await self._finalize(call, result, record, "rejected", started_at)

        if not self.is_available(call.name):
            reason = "tool was not advertised for the current model request"
            result = _error_result(
                f"{reason}: {call.name}",
                ToolError(
                    "TOOL_UNAVAILABLE",
                    "policy",
                    reason,
                    not_started=True,
                ),
            )
            _record_event(record, "failed", error=result.details["error"])
            return await self._finalize(call, result, record, "rejected", started_at)

        spec = self.spec(call.name)
        try:
            _raise_cancelled(cancellation_token, "prepare")
            arguments = copy.deepcopy(call.arguments)
            prepare = getattr(tool, "prepare_arguments", None)
            if prepare is not None:
                prepared = prepare(arguments)
                arguments = await prepared if inspect.isawaitable(prepared) else prepared
            self._validate(arguments, tool.input_schema)
            record["arguments_summary"] = _summarize_arguments(arguments)
            idempotent = _call_is_idempotent(call.name, arguments, spec)
            record["idempotent"] = idempotent
            _record_event(record, "validated")
        except ToolCancelledError as exc:
            return await self._finish_cancelled(
                call, record, started_at, exc, stage=exc.stage or "prepare", spec=spec
            )
        except Exception as exc:
            result = _error_result(
                f"{type(exc).__name__}: {exc}",
                ToolError("INVALID_ARGUMENT", "validation", str(exc), not_started=True),
            )
            _record_event(record, "failed", error=result.details["error"])
            return await self._finalize(call, result, record, "rejected", started_at)

        prepared_call = ToolInvocation(call.call_id, call.name, arguments)

        # Authorization is a logical-call decision. Retrying a safe transient
        # operation must not ask for approval again or rerun policy hooks.
        try:
            _raise_cancelled(cancellation_token, "preflight")
            preflight_started = time.perf_counter()
            blocked: ToolResult | None = None
            for preflight in self.preflights:
                _raise_cancelled(cancellation_token, "preflight")
                checked = preflight(prepared_call)
                checked = (
                    await _await_cancelable(
                        checked,
                        cancellation_token,
                        timeout=None,
                        stage="preflight",
                    )
                    if inspect.isawaitable(checked)
                    else checked
                )
                if checked is not None:
                    if not isinstance(checked, ToolResult):
                        raise TypeError("preflight must return ToolResult or None")
                    blocked = checked
                    break
            _record_event(
                record,
                "preflighted",
                started_perf=preflight_started,
                details={"blocked": blocked is not None},
            )
        except ToolCancelledError as exc:
            return await self._finish_cancelled(
                call, record, started_at, exc, stage=exc.stage or "preflight", spec=spec
            )
        except Exception as exc:
            result = _error_result(
                f"{type(exc).__name__}: {exc}",
                ToolError("PREFLIGHT_FAILED", "preflight", str(exc), not_started=True),
            )
            _record_event(
                record,
                "failed",
                error=result.details["error"],
                details={"blocked": True},
            )
            return await self._finalize(call, result, record, "rejected", started_at)

        if blocked is not None:
            result = _normalize_preflight_result(blocked)
            _record_event(
                record,
                "failed",
                error=result.details.get("error"),
                details={"not_started": True, "blocked": True},
            )
            return await self._finalize(call, result, record, "rejected", started_at)

        repeat_key = _repeatable_call_key(call.name, arguments, self._mutation_epoch)
        verification_key = (
            self._verification_key(call, arguments)
            if call.name == "bash" and repeat_key is not None
            else None
        )
        if verification_key is not None and verification_key in self._verification_cache:
            cached = self._verification_cache[verification_key]
            result = ToolResult(
                content=str(cached.get("content") or ""),
                is_error=bool(cached.get("is_error", False)),
                details={**(cached.get("details") or {}), "cached_reuse": True},
            )
            outcome = "succeeded" if not result.is_error else "failed"
            _record_event(record, "succeeded" if not result.is_error else "failed",
                          details={"cached_reuse": True, "persistent_verification": True})
            return await self._finalize(call, result, record, outcome, started_at, retry_count=0)
        if repeat_key is not None and repeat_key in self._repeatable_results:
            result = copy.deepcopy(self._repeatable_results[repeat_key])
            result.content = _cached_reuse_content(call.name)
            result.details = {**result.details, "cached_reuse": True}
            outcome = "succeeded"
            _record_event(record, "succeeded", details={"cached_reuse": True})
            return await self._finalize(
                call, result, record, outcome, started_at, retry_count=0
            )

        result: ToolResult | None = None
        outcome: ToolOutcome = "failed"
        retry_count = 0
        retry_limit = _retry_limit(spec, self.max_retries, idempotent)

        for attempt in range(retry_limit + 1):
            attempt_record: dict[str, Any] = {"attempt": attempt, "started": False}
            record["attempts"].append(attempt_record)
            current_stage = "execution"
            try:
                _raise_cancelled(cancellation_token, "execution")
                if attempt == 0:
                    _record_event(record, "started", attempt=attempt)
                attempt_record["started"] = True
                record["started"] = True
                execution = _invoke_tool(
                    tool,
                    arguments,
                    cancellation_token,
                    self._tool_context(cancellation_token),
                )
                result = await _await_cancelable(
                    execution,
                    cancellation_token,
                    timeout=self.timeout_seconds,
                    stage="execution",
                )
                if not isinstance(result, ToolResult):
                    raise TypeError("tool execution must return ToolResult")

                if result.is_error:
                    result = _normalize_result_error(result, "execution")
                    if _is_network_result(result) and attempt < retry_limit:
                        retry_count = _schedule_retry(
                            record,
                            attempt_record,
                            result,
                            attempt,
                            retry_count,
                            self.retry_base_seconds,
                            self.retry_max_seconds,
                            idempotent,
                        )
                        await _sleep_cancelable(
                            attempt_record["backoff_seconds"], cancellation_token
                        )
                        continue
                    outcome = "failed"
                    if _is_network_result(result) and not idempotent:
                        _mark_uncertain(record, result, spec)
                    _record_event(
                        record,
                        "failed",
                        attempt=attempt,
                        error=result.details.get("error"),
                    )
                    break

                outcome = "succeeded"
                if verification_key is not None and not result.is_error:
                    self._verification_cache[verification_key] = {
                        "content": result.content,
                        "is_error": result.is_error,
                        "details": result.details,
                    }
                    self._save_verification_cache()
                if repeat_key is not None:
                    self._repeatable_results[repeat_key] = copy.deepcopy(result)
                if call.name in {"write", "edit"}:
                    self._mutation_epoch += 1
                    self._repeatable_results.clear()
                    self._save_verification_cache()
                _record_event(
                    record,
                    "succeeded",
                    attempt=attempt,
                    details={"output_chars": len(str(result.content))},
                )
                break
            except ToolCancelledError as exc:
                return await self._finish_cancelled(
                    call,
                    record,
                    started_at,
                    exc,
                    stage=exc.stage or current_stage,
                    spec=spec,
                    retry_count=retry_count,
                )
            except asyncio.CancelledError:
                exc = ToolCancelledError("asyncio task cancelled", stage=current_stage)
                return await self._finish_cancelled(
                    call,
                    record,
                    started_at,
                    exc,
                    stage=current_stage,
                    spec=spec,
                    retry_count=retry_count,
                    reraises_asyncio=True,
                )
            except asyncio.TimeoutError as exc:
                outcome = "timed_out"
                result = _error_result(
                    _timeout_message(exc, self.timeout_seconds),
                    ToolError(
                        "TIMEOUT",
                        current_stage,
                        str(exc) or "tool execution timed out",
                        not_started=not attempt_record["started"],
                        uncertain_side_effect=record["started"] and spec.side_effect != "none",
                    ),
                )
                _record_event(
                    record,
                    "timed_out",
                    attempt=attempt,
                    error=result.details["error"],
                )
                break
            except Exception as exc:
                is_network = _is_network_error(exc)
                retryable = is_network and attempt < retry_limit
                result = _error_result(
                    f"{type(exc).__name__}: {exc}",
                    ToolError(
                        _execution_error_code(exc, is_network),
                        current_stage,
                        str(exc),
                        retryable=retryable,
                        not_started=not attempt_record["started"],
                        uncertain_side_effect=record["started"] and spec.side_effect != "none",
                    ),
                )
                if retryable:
                    retry_count = _schedule_retry(
                        record,
                        attempt_record,
                        result,
                        attempt,
                        retry_count,
                        self.retry_base_seconds,
                        self.retry_max_seconds,
                        idempotent,
                    )
                    await _sleep_cancelable(
                        attempt_record["backoff_seconds"], cancellation_token
                    )
                    continue
                outcome = "failed"
                if is_network and not idempotent:
                    _mark_uncertain(record, result, spec)
                _record_event(
                    record,
                    "failed",
                    attempt=attempt,
                    error=result.details["error"],
                )
                break

        if result is None:
            result = _error_result(
                "tool did not produce a result",
                ToolError(
                    "TOOL_EXECUTION_FAILED",
                    "execution",
                    "missing result",
                    not_started=not record["started"],
                ),
            )
            _record_event(record, "failed", error=result.details["error"])
        return await self._finalize(
            call,
            result,
            record,
            outcome,
            started_at,
            retry_count=retry_count,
        )

    async def _finish_cancelled(
        self,
        call: ToolInvocation,
        record: dict[str, Any],
        started_at: float,
        exc: ToolCancelledError,
        *,
        stage: str,
        spec: ToolSpec,
        retry_count: int = 0,
        reraises_asyncio: bool = False,
    ) -> ToolResult:
        uncertain = bool(record["started"] and spec.side_effect != "none")
        error = ToolError(
            "CANCELLED",
            stage,
            str(exc) or "tool call cancelled",
            not_started=not record["started"],
            uncertain_side_effect=uncertain,
        )
        record["uncertain_side_effect"] = bool(
            record["uncertain_side_effect"] or uncertain
        )
        _record_event(record, "cancelled", error=error.to_dict())
        result = _error_result(f"[CANCELLED] Tool call was not completed: {exc}", error)
        if exc.details:
            result.details.update(copy.deepcopy(exc.details))
            result.details["error"] = error.to_dict()
        await self._finalize(
            call,
            result,
            record,
            "cancelled",
            started_at,
            retry_count=retry_count,
        )
        if reraises_asyncio:
            raise asyncio.CancelledError
        raise exc

    def _tool_context(self, cancellation_token: CancellationToken | None) -> ToolContext:
        return ToolContext(
            cancellation_token=cancellation_token,
            workspace=self.context.workspace,
            session_id=self.context.session_id,
            trace_id=self.context.trace_id,
            timeout_seconds=self.timeout_seconds,
            approval_state=self.context.approval_state,
            environment=dict(self.context.environment),
            artifact_store=self.context.artifact_store,
        )

    async def _finalize(
        self,
        call: ToolInvocation,
        result: ToolResult,
        record: dict[str, Any],
        outcome: ToolOutcome,
        started_at: float,
        *,
        retry_count: int = 0,
    ) -> ToolResult:
        """Apply transforms and one output bound, then deliver exactly once."""

        result = _normalize_result_error(result, "execution") if result.is_error else result
        transform_error: ToolError | None = None
        for transform in self.result_transforms:
            try:
                transformed = transform(call, result)
                transformed = await transformed if inspect.isawaitable(transformed) else transformed
                if not isinstance(transformed, ToolResult):
                    raise TypeError("result transform must return ToolResult")
                result = transformed
            except Exception as exc:
                transform_error = ToolError(
                    "RESULT_TRANSFORM_FAILED", "transform", str(exc)
                )
                result = ToolResult(
                    content=str(getattr(result, "content", "")),
                    is_error=True,
                    details={
                        **(
                            getattr(result, "details", {})
                            if isinstance(getattr(result, "details", {}), dict)
                            else {}
                        ),
                        "error": transform_error.to_dict(),
                        "result_transform_error": f"{type(exc).__name__}: {exc}",
                    },
                )
                outcome = "failed"
                _record_event(
                    record,
                    "failed",
                    error=transform_error.to_dict(),
                    details={"transform_error": True},
                )
                break

        if not isinstance(result.content, str):
            result.content = str(result.content)
        raw_chars = len(result.content)
        raw_bytes = len(result.content.encode("utf-8", errors="replace"))
        result_details = result.details if isinstance(result.details, dict) else {}
        result.details = result_details
        tool_truncated = bool(result_details.get("truncated"))
        delivered_chars = raw_chars
        truncated = False
        omitted = 0
        # Preserve original tool errors verbatim. Successful oversized output
        # is bounded by the tool/artifact pipeline, but an error message is
        # corrective evidence that the model may need to inspect exactly.
        bounded: str | None = None
        limit_label: str
        unit_label: str
        if self.max_output_chars is not None:
            if raw_chars > self.max_output_chars:
                bounded = result.content[: self.max_output_chars]
                omitted = raw_chars - self.max_output_chars
            limit_label = str(self.max_output_chars)
            unit_label = "characters"
        else:
            if raw_bytes > self.max_output_bytes:
                bounded_result = truncate_head(
                    result.content,
                    max_lines=max(1, raw_chars + 1),
                    max_bytes=self.max_output_bytes,
                )
                bounded = bounded_result.content
                omitted = raw_bytes - bounded_result.output_bytes
            limit_label = str(self.max_output_bytes)
            unit_label = "bytes"
        if bounded is not None and not result.is_error:
            truncated = True
            result.content = (
                bounded
                + f"\n[truncated {omitted} {unit_label}; use read/grep/search to inspect the relevant details]"
            )
            delivered_chars = len(result.content)
            truncation_error = ToolError(
                "OUTPUT_TRUNCATED",
                "transform",
                f"output exceeded {limit_label} {unit_label}",
            ).to_dict()
            # Truncation is a bounded successful delivery, not an execution
            # failure. ``error`` remains as a compatibility warning; new code
            # should prefer ``warning``.
            result.details = {
                **result_details,
                "warning": truncation_error,
                "error": result_details.get("error", truncation_error),
                "truncated": True,
                "omitted_chars": omitted if self.max_output_chars is not None else 0,
                "omitted_bytes": omitted if self.max_output_chars is None else 0,
                "truncation": {
                    "limit_chars": self.max_output_chars,
                    "limit_bytes": self.max_output_bytes if self.max_output_chars is None else None,
                    "omitted_chars": omitted if self.max_output_chars is not None else 0,
                    "omitted_bytes": omitted if self.max_output_chars is None else 0,
                },
            }

        effective_truncated = truncated or tool_truncated
        record["truncated"] = effective_truncated
        record["output"] = {
            "raw_chars": raw_chars,
            "raw_bytes": raw_bytes,
            "delivered_chars": delivered_chars,
            "delivered_bytes": len(result.content.encode("utf-8", errors="replace")),
            "omitted_chars": omitted,
            "truncated": truncated,
        }
        if transform_error is not None:
            record["transform_error"] = transform_error.to_dict()
        _record_event(
            record,
            "transformed",
            details={
                "output_chars": delivered_chars,
                "output_bytes": len(result.content.encode("utf-8", errors="replace")),
                "truncated": truncated,
            },
        )
        _record_event(
            record,
            "delivered",
            details={"output_chars": delivered_chars, "is_error": result.is_error},
        )

        record["delivery_count"] = int(record.get("delivery_count", 0)) + 1
        _complete_record(record, outcome, started_at, retry_count, result)
        result.details = {
            **result.details,
            "call_id": call.call_id,
            "status": outcome,
            "phase": "delivered",
            # Compatibility names are kept at the result boundary only; the
            # internal trace has one canonical status and one output object.
            "lifecycle": outcome,
            "lifecycle_phase": "delivered",
            "retry_count": retry_count,
            "idempotent": bool(record.get("idempotent", False)),
            "started": bool(record["started"]),
            "not_started": not record["started"],
            "truncated": effective_truncated,
            "uncertain_side_effect": bool(record.get("uncertain_side_effect", False)),
            "output_chars": delivered_chars,
            "output_bytes": len(result.content.encode("utf-8", errors="replace")),
            "delivery_count": record["delivery_count"],
            "trace": copy.deepcopy(record),
        }
        self._call_history.append(copy.deepcopy(record))
        return result

    async def startup(self) -> None:
        for tool in self._tool_map.values():
            hook = getattr(tool, "startup", None)
            if hook is not None:
                value = hook()
                if inspect.isawaitable(value):
                    await value

    async def shutdown(self) -> None:
        for tool in reversed(list(self._tool_map.values())):
            hook = getattr(tool, "shutdown", None) or getattr(tool, "cleanup", None)
            if hook is not None:
                value = hook()
                if inspect.isawaitable(value):
                    await value

    @staticmethod
    def _validate(arguments: dict[str, Any], schema: dict[str, Any]) -> None:
        ToolExecutor._validate_value(arguments, schema, "arguments")

    @staticmethod
    def _validate_value(value: Any, schema: dict[str, Any], location: str) -> None:
        expected_name = schema.get("type")
        valid = True
        if expected_name == "string":
            valid = isinstance(value, str)
        elif expected_name == "integer":
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif expected_name == "number":
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif expected_name == "boolean":
            valid = isinstance(value, bool)
        elif expected_name == "object":
            valid = isinstance(value, dict)
        elif expected_name == "array":
            valid = isinstance(value, list)
        if not valid:
            raise TypeError(f"{location} must be {expected_name}")

        allowed = schema.get("enum")
        if allowed is not None and value not in allowed:
            raise ValueError(f"{location} must be one of: {', '.join(map(str, allowed))}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = schema.get("minimum")
            if minimum is not None and value < minimum:
                raise ValueError(f"{location} must be at least {minimum}")
            maximum = schema.get("maximum")
            if maximum is not None and value > maximum:
                raise ValueError(f"{location} must be at most {maximum}")

        if isinstance(value, str):
            minimum_length = schema.get("minLength")
            if minimum_length is not None and len(value) < minimum_length:
                raise ValueError(
                    f"{location} must contain at least {minimum_length} character(s)"
                )
            maximum_length = schema.get("maxLength")
            if maximum_length is not None and len(value) > maximum_length:
                raise ValueError(
                    f"{location} must contain at most {maximum_length} character(s)"
                )

        if isinstance(value, list):
            minimum_items = schema.get("minItems")
            if minimum_items is not None and len(value) < minimum_items:
                raise ValueError(
                    f"{location} must contain at least {minimum_items} item(s)"
                )
            item_schema = schema.get("items")
            if item_schema:
                for index, item in enumerate(value):
                    ToolExecutor._validate_value(item, item_schema, f"{location}[{index}]")

        if isinstance(value, dict):
            required = schema.get("required") or []
            missing = [name for name in required if name not in value]
            if missing:
                raise ValueError(
                    f"missing required arguments at {location}: {', '.join(missing)}"
                )
            properties = schema.get("properties") or {}
            if schema.get("additionalProperties") is False:
                extras = [name for name in value if name not in properties]
                if extras:
                    raise ValueError(
                        f"unexpected arguments at {location}: {', '.join(extras)}"
                    )
            for name, item in value.items():
                child_schema = properties.get(name)
                if child_schema:
                    ToolExecutor._validate_value(item, child_schema, f"{location}.{name}")


def _invoke_tool(
    tool: Tool,
    arguments: dict[str, Any],
    cancellation_token: CancellationToken | None,
    context: ToolContext,
) -> Any:
    try:
        parameters = inspect.signature(tool.execute).parameters
    except (TypeError, ValueError):
        parameters = {}
    kwargs: dict[str, Any] = {}
    if _supports_keyword(parameters, "cancellation_token"):
        kwargs["cancellation_token"] = cancellation_token
    if _supports_keyword(parameters, "context"):
        kwargs["context"] = context
    return tool.execute(arguments, **kwargs)  # type: ignore[call-arg]


def _supports_keyword(parameters: Mapping[str, inspect.Parameter], name: str) -> bool:
    parameter = parameters.get(name)
    if parameter is not None and parameter.kind is not inspect.Parameter.POSITIONAL_ONLY:
        return True
    return any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values())


def _new_record(call: ToolInvocation) -> dict[str, Any]:
    return {
        "call_id": call.call_id,
        "tool_name": call.name,
        "arguments_summary": _summarize_arguments(call.arguments),
        "events": [],
        "attempts": [],
        "started": False,
        "status": "received",
        "delivery_count": 0,
        "truncated": False,
        "uncertain_side_effect": False,
    }


def _complete_record(
    record: dict[str, Any],
    outcome: ToolOutcome,
    started_at: float,
    retry_count: int,
    result: ToolResult,
) -> None:
    record["status"] = outcome
    record["outcome"] = outcome  # legacy history readers
    record["retry_count"] = retry_count
    record["duration_ms"] = round((time.perf_counter() - started_at) * 1000, 3)
    record["is_error"] = result.is_error
    error = result.details.get("error") if isinstance(result.details, dict) else None
    warning = result.details.get("warning") if isinstance(result.details, dict) else None
    if isinstance(error, dict) and result.is_error:
        record["error"] = copy.deepcopy(error)
        record["error_stage"] = error.get("stage")
    elif isinstance(warning, dict):
        record["warning"] = copy.deepcopy(warning)


def _record_event(
    record: dict[str, Any],
    phase: str,
    *,
    started_perf: float | None = None,
    attempt: int | None = None,
    error: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    ended_perf = time.perf_counter()
    duration_ms = (
        round((ended_perf - started_perf) * 1000, 3)
        if started_perf is not None
        else 0.0
    )
    ended_at = time.time()
    event: dict[str, Any] = {
        "phase": phase,
        "started_at": ended_at - duration_ms / 1000.0,
        "ended_at": ended_at,
        "duration_ms": duration_ms,
    }
    if attempt is not None:
        event["attempt"] = attempt
    if error is not None:
        event["error"] = copy.deepcopy(error)
    if details:
        event.update(copy.deepcopy(details))
    record["events"].append(event)


def _summarize_arguments(arguments: Any, limit: int = 2_000) -> str:
    try:
        rendered = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=repr)
    except Exception:
        rendered = repr(arguments)
    if len(rendered) <= limit:
        return rendered
    return rendered[:limit] + f"…[summary truncated {len(rendered) - limit} chars]"


def _error_result(content: str, error: ToolError) -> ToolResult:
    return ToolResult(content=content, is_error=True, details={"error": error.to_dict()})


def _normalize_result_error(result: ToolResult, stage: str) -> ToolResult:
    if not result.is_error:
        return result
    details = dict(result.details or {})
    existing = details.get("error")
    if isinstance(existing, dict) and existing.get("code"):
        details["error"] = _complete_error(existing, stage, str(result.content))
        result.details = details
        return result
    approval = details.get("approval")
    if isinstance(approval, dict):
        decision = str(approval.get("decision", ""))
        code = {
            "denied-by-policy": "POLICY_DENIED",
            "denied": "APPROVAL_REQUIRED",
            "timeout": "APPROVAL_REQUIRED",
            "unavailable": "APPROVAL_REQUIRED",
            "call-mismatch": "POLICY_DENIED",
        }.get(decision, "APPROVAL_REQUIRED")
        details["error"] = ToolError(
            code, "preflight", str(result.content), not_started=True
        ).to_dict()
    else:
        code = (
            "NETWORK_ERROR"
            if _is_network_message(str(result.content))
            else "TOOL_EXECUTION_FAILED"
        )
        details["error"] = ToolError(code, stage, str(result.content)).to_dict()
    result.details = details
    return result


def _complete_error(value: dict[str, Any], stage: str, fallback_message: str) -> dict[str, Any]:
    return {
        "code": str(value.get("code") or "TOOL_EXECUTION_FAILED"),
        "stage": str(value.get("stage") or stage),
        "message": str(value.get("message") or fallback_message),
        "retryable": bool(value.get("retryable", False)),
        "not_started": bool(value.get("not_started", False)),
        "uncertain_side_effect": bool(value.get("uncertain_side_effect", False)),
    }


def _normalize_preflight_result(result: ToolResult) -> ToolResult:
    if result.is_error:
        return _normalize_result_error(result, "preflight")
    return ToolResult(
        content=result.content,
        is_error=True,
        details={
            **result.details,
            "error": ToolError(
                "POLICY_DENIED", "preflight", str(result.content), not_started=True
            ).to_dict(),
        },
    )


def _retry_limit(spec: ToolSpec, configured: int, idempotent: bool) -> int:
    if not idempotent:
        return 0
    policy = spec.retry_policy or {}
    if policy.get("enabled") is False:
        return 0
    raw = policy.get("max_retries", configured)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = configured
    # A tool cannot turn a model mistake into an unbounded hot loop.
    return min(5, max(0, value))


def _schedule_retry(
    record: dict[str, Any],
    attempt_record: dict[str, Any],
    result: ToolResult,
    attempt: int,
    retry_count: int,
    base: float,
    maximum: float,
    idempotent: bool,
) -> int:
    _mark_error_retryable(result)
    delay = _retry_delay(base, maximum, retry_count)
    uncertain = not idempotent
    record["uncertain_side_effect"] = bool(
        record.get("uncertain_side_effect", False) or uncertain
    )
    attempt_record.update(
        {
            "retryable": True,
            "retry_reason": "network_error",
            "backoff_seconds": delay,
            "uncertain_side_effect": uncertain,
        }
    )
    _record_event(
        record,
        "retry_scheduled",
        attempt=attempt,
        details={
            "reason": "network_error",
            "backoff_seconds": delay,
            "retry_number": retry_count + 1,
            "uncertain_side_effect": uncertain,
        },
    )
    return retry_count + 1


def _mark_uncertain(record: dict[str, Any], result: ToolResult, spec: ToolSpec) -> None:
    uncertain = bool(record.get("started") and spec.side_effect != "none")
    record["uncertain_side_effect"] = bool(
        record.get("uncertain_side_effect", False) or uncertain
    )
    error = result.details.get("error") if isinstance(result.details, dict) else None
    if isinstance(error, dict) and uncertain:
        result.details["error"] = {**error, "uncertain_side_effect": True}


def _mark_error_retryable(result: ToolResult) -> None:
    error = result.details.get("error") if isinstance(result.details, dict) else None
    if isinstance(error, dict):
        result.details["error"] = {**error, "retryable": True}


def _retry_delay(base: float, maximum: float, retry_index: int) -> float:
    safe_base = max(0.0, float(base))
    safe_maximum = max(safe_base, float(maximum))
    return min(safe_maximum, safe_base * (2**retry_index))


def _raise_cancelled(token: CancellationToken | None, stage: str) -> None:
    if token is not None:
        token.raise_if_tool_cancelled(stage=stage)


async def _sleep_cancelable(delay: float, cancellation_token: CancellationToken | None) -> None:
    if delay <= 0:
        _raise_cancelled(cancellation_token, "retry_backoff")
        return
    sleep_task = asyncio.create_task(asyncio.sleep(delay))
    cancel_task = (
        asyncio.create_task(cancellation_token.wait())
        if cancellation_token is not None
        else None
    )
    waiters = {sleep_task}
    if cancel_task is not None:
        waiters.add(cancel_task)
    try:
        done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if cancel_task is not None and cancel_task in done:
            raise ToolCancelledError(cancellation_token.reason, stage="retry_backoff")
    finally:
        for task in waiters:
            if not task.done():
                task.cancel()
        for task in waiters:
            with contextlib.suppress(BaseException):
                await task


async def _await_cancelable(
    awaitable: Any,
    cancellation_token: CancellationToken | None,
    *,
    timeout: float | None,
    stage: str,
) -> Any:
    if not inspect.isawaitable(awaitable):
        _raise_cancelled(cancellation_token, stage)
        return awaitable
    operation = asyncio.ensure_future(awaitable)
    if cancellation_token is not None:
        try:
            cancellation_token.raise_if_tool_cancelled(stage=stage)
        except BaseException:
            operation.cancel()
            with contextlib.suppress(BaseException):
                await operation
            raise
    cancellation = (
        asyncio.create_task(cancellation_token.wait())
        if cancellation_token is not None
        else None
    )
    waiters = {operation}
    if cancellation is not None:
        waiters.add(cancellation)
    try:
        done, _ = await asyncio.wait(
            waiters,
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if operation in done:
            return operation.result()
        operation.cancel()
        with contextlib.suppress(BaseException):
            await operation
        if cancellation is not None and cancellation in done:
            raise ToolCancelledError(cancellation_token.reason, stage=stage)
        raise asyncio.TimeoutError
    except asyncio.CancelledError:
        operation.cancel()
        with contextlib.suppress(BaseException):
            await operation
        raise
    finally:
        if cancellation is not None:
            cancellation.cancel()
            with contextlib.suppress(BaseException):
                await cancellation


def _timeout_message(exc: BaseException, timeout: float | None) -> str:
    if str(exc):
        return str(exc)
    if timeout is not None:
        return f"tool timed out after {timeout:g} seconds"
    return "tool execution timed out"


def _is_network_error(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    text = f"{type(exc).__name__}: {exc}".casefold()
    return any(
        token in text
        for token in (
            "connection",
            "connect",
            "network",
            "readerror",
            "writeerror",
            "connectionreset",
            "connectionaborted",
            "temporarily unavailable",
            "dns",
        )
    )


def _execution_error_code(exc: BaseException, is_network: bool) -> str:
    if is_network:
        return "NETWORK_ERROR"
    if isinstance(exc, PermissionError):
        return "POLICY_DENIED"
    if isinstance(exc, FileNotFoundError):
        return "NOT_FOUND"
    if isinstance(exc, IsADirectoryError):
        return "INVALID_TARGET"
    return "TOOL_EXECUTION_FAILED"


def _is_network_result(result: ToolResult) -> bool:
    error = result.details.get("error") if isinstance(result.details, dict) else None
    if isinstance(error, dict) and str(error.get("code", "")).upper() in {
        "NETWORK_ERROR",
        "CONNECTION_ERROR",
        "TEMPORARY_UNAVAILABLE",
    }:
        return True
    return _is_network_message(result.content)


def _is_network_message(message: str) -> bool:
    text = str(message).casefold()
    if any(
        token in text
        for token in (
            "connection",
            "connect",
            "network",
            "readerror",
            "writeerror",
            "connectionreset",
            "connectionaborted",
            "temporarily unavailable",
            "dns",
        )
    ):
        return True
    normalized = text.lstrip()
    return normalized.startswith(("timeouterror:", "readtimeout", "connecttimeout"))


def _tool_sort_key(name: str) -> tuple[int, str]:
    return (_TOOL_ORDER.get(name, 100), name)


def _call_is_idempotent(
    name: str,
    arguments: dict[str, Any],
    spec: ToolSpec,
) -> bool:
    if spec.idempotent:
        return True
    if name in _READ_ONLY_TOOLS:
        return True
    # Memory and Goal are multiplexed tools: only their read actions are safe
    # to replay after an uncertain transport failure.
    if name == "memory":
        return arguments.get("action") == "search"
    if name == "goal":
        return arguments.get("action") == "status"
    return False


_REPEATABLE_CHECK = re.compile(
    r"(?:pytest|unittest|verify|(?:^|\s)test(?:\s|$)|check|compile|lint|assert)",
    re.IGNORECASE,
)
_MUTATING_SHELL = re.compile(
    r"(?:>>?|\b(?:rm|mv|cp|mkdir|rmdir|touch|chmod|chown)\b|"
    r"\b(?:git\s+(?:commit|reset|checkout|clean))\b|\b(?:sed|perl)\s+-i\b)",
    re.IGNORECASE,
)


def _repeatable_call_key(
    name: str,
    arguments: dict[str, Any],
    mutation_epoch: int,
) -> str | None:
    """Cache only read-only evidence until a known file mutation occurs."""
    if name in _READ_ONLY_TOOLS:
        payload = arguments
    elif name == "bash":
        command = str(arguments.get("command") or "")
        if not _REPEATABLE_CHECK.search(command) or _MUTATING_SHELL.search(command):
            return None
        payload = {"command": command}
    else:
        return None
    return f"{mutation_epoch}:{name}:{json.dumps(payload, sort_keys=True, ensure_ascii=False)}"


def _cached_reuse_content(name: str) -> str:
    """Return a small model-facing result for an unchanged repeatable call.

    The original result remains available in the executor history and trace;
    sending it again only makes the model reread identical evidence.
    """

    return (
        f"[{name}] 与上次结果一致，工作区未发生变化。"
        "如需新的证据，请改变调用参数或先修改文件。"
    )


__all__ = [
    "Preflight",
    "ResultTransform",
    "TOOL_LIFECYCLE_PHASES",
    "ToolExecutor",
]
