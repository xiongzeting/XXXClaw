from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from MiniClaw.coding_agent.memory.locking import MemoryFileLock


TRACE_SCHEMA_VERSION = 1
MAX_TRACE_STRING_CHARS = 200_000
REDACTED_KEY = re.compile(
    r"^(?:api[-_]?key|authorization|cookie|password|secret|token|access[-_]?token|refresh[-_]?token|credential|private[-_]?key)$",
    re.IGNORECASE,
)
TOKEN_PATTERNS = [
    (re.compile(r"\b(?:sk|xox[abprs])-[A-Za-z0-9_-]{12,}\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]+=*", re.IGNORECASE), "Bearer [REDACTED]"),
    (
        re.compile(
            r"\b([A-Z0-9_]*(?:API_KEY|TOKEN|PASSWORD|SECRET))\s*=\s*([^\s;&|]+)",
            re.IGNORECASE,
        ),
        r"\1=[REDACTED]",
    ),
]


class TraceRecorder:
    """Append-only unified trace. Trace failures never break the coding run."""

    def __init__(
        self,
        session_dir: str | Path,
        channel_id: str,
        conversation_id: str,
    ) -> None:
        self.path = Path(session_dir) / "trace.jsonl"
        identity = f"{channel_id}\0{conversation_id}".encode("utf-8")
        self.trace_id = hashlib.sha256(identity).hexdigest()[:32]
        self.channel_id = channel_id
        self.conversation_id = conversation_id
        self.last_error: str | None = None
        self._run_records: dict[str, list[dict[str, Any]]] = {}

    def new_run_id(self) -> str:
        return uuid.uuid4().hex

    def record(
        self,
        event_type: str,
        data: Any,
        *,
        run_id: str | None = None,
        parent_event_id: str | None = None,
        timestamp: str | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        record = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "trace_id": self.trace_id,
            "event_id": event_id,
            "type": event_type,
            "timestamp": timestamp or utc_now(),
            "channel_id": self.channel_id,
            "conversation_id": self.conversation_id,
            "run_id": run_id,
            "parent_event_id": parent_event_id,
            "data": sanitize_trace_value(data),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with MemoryFileLock(self.path):
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            if run_id:
                self._run_records.setdefault(run_id, []).append(record)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
        return event_id

    def run_metrics(self, run_id: str) -> dict[str, Any]:
        records = self._run_records.get(run_id, [])
        model_events = [record for record in records if record["type"] == "model.request"]
        tool_events = [record for record in records if record["type"] == "tool.call"]
        transport_events = [record for record in records if record["type"] == "model.transport"]
        compactions = [record for record in records if record["type"] == "compaction.completed"]
        usage = zero_usage()
        for record in model_events:
            usage = add_usage(usage, record["data"].get("usage", {}))
        return {
            "usage": usage,
            "model_requests": len(model_events),
            "model_errors": sum(record["data"].get("status") == "error" for record in model_events),
            "model_retries": sum(
                record["data"].get("phase") == "retry_scheduled" for record in transport_events
            ),
            "model_fallbacks": sum(
                record["data"].get("phase") == "fallback_selected" for record in transport_events
            ),
            "tool_calls": len(tool_events),
            "tool_errors": sum(record["data"].get("status") == "error" for record in tool_events),
            "tool_cancelled": sum(
                record["data"].get("status") == "cancelled" for record in tool_events
            ),
            "compactions": len(compactions),
            "tokens_saved_by_compaction": sum(
                max(
                    0,
                    int(record["data"].get("tokens_before", 0))
                    - int(record["data"].get("tokens_after", 0)),
                )
                for record in compactions
            ),
        }

    def close_run(self, run_id: str) -> None:
        self._run_records.pop(run_id, None)

    def record_approval(
        self,
        *,
        run_id: str,
        tool_name: str,
        risk: str,
        reason: str,
        preview: str,
        decision: str,
        approval_id: str | None = None,
        tool_call_id: str | None = None,
        level: str | None = None,
        capabilities: tuple[str, ...] = (),
        normalized_call_hash: str | None = None,
    ) -> str:
        return self.record(
            "approval.decision",
            {
                "approval_id": approval_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "risk": risk,
                "level": level,
                "capabilities": capabilities,
                "normalized_call_hash": normalized_call_hash,
                "reason": reason,
                "preview": preview,
                "decision": decision,
            },
            run_id=run_id,
        )

    def record_approval_request(
        self,
        *,
        run_id: str,
        approval_id: str,
        tool_call_id: str,
        tool_name: str,
        risk: str,
        level: str,
        reason: str,
        preview: str,
        capabilities: tuple[str, ...] = (),
        normalized_call_hash: str | None = None,
    ) -> str:
        return self.record(
            "approval.requested",
            {
                "approval_id": approval_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "risk": risk,
                "level": level,
                "capabilities": capabilities,
                "normalized_call_hash": normalized_call_hash,
                "reason": reason,
                "preview": preview,
            },
            run_id=run_id,
        )


def read_trace_records(path: str | Path) -> list[dict[str, Any]]:
    trace_path = Path(path)
    if not trace_path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(value, dict)
            and value.get("schema_version") == TRACE_SCHEMA_VERSION
            and isinstance(value.get("type"), str)
            and isinstance(value.get("event_id"), str)
        ):
            records.append(value)
    return records


def sanitize_trace_value(value: Any, key: str | None = None, seen: set[int] | None = None) -> Any:
    if key and REDACTED_KEY.match(key):
        return "[REDACTED]"
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        return sanitize_string(value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, bytes):
        return f"[binary omitted: {len(value)} bytes]"
    if not isinstance(value, (dict, list, tuple)):
        return sanitize_string(str(value))
    active = seen or set()
    identity = id(value)
    if identity in active:
        return "[CIRCULAR]"
    active.add(identity)
    try:
        if isinstance(value, (list, tuple)):
            return [sanitize_trace_value(item, seen=active) for item in value]
        output: dict[str, Any] = {}
        for child_key, child_value in value.items():
            name = str(child_key)
            if name == "data" and isinstance(child_value, str) and len(child_value) > 1_024:
                output[name] = f"[binary/image data omitted: {len(child_value)} chars]"
            else:
                output[name] = sanitize_trace_value(child_value, name, active)
        return output
    finally:
        active.remove(identity)


def sanitize_string(value: str) -> str:
    sanitized = value
    for pattern, replacement in TOKEN_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    if len(sanitized) > MAX_TRACE_STRING_CHARS:
        sanitized = f"{sanitized[:MAX_TRACE_STRING_CHARS]}…[trace truncated]"
    return sanitized


def hash_json(value: Any) -> str:
    serialized = json.dumps(sanitize_trace_value(value), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def usage_dict(
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    *,
    input_cost_per_million: float = 0.0,
    output_cost_per_million: float = 0.0,
    cached_input_cost_per_million: float = 0.0,
) -> dict[str, int | float]:
    uncached = max(0, input_tokens - cached_tokens)
    cost = (
        uncached * input_cost_per_million
        + cached_tokens * cached_input_cost_per_million
        + output_tokens * output_cost_per_million
    ) / 1_000_000
    return {
        "input_tokens": max(0, input_tokens),
        "output_tokens": max(0, output_tokens),
        "cached_tokens": max(0, cached_tokens),
        "total_tokens": max(0, input_tokens) + max(0, output_tokens),
        "cost_usd": round(cost, 8),
    }


def zero_usage() -> dict[str, int | float]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
    }


def add_usage(left: dict[str, Any], right: dict[str, Any]) -> dict[str, int | float]:
    return {
        "input_tokens": int(left.get("input_tokens", 0)) + int(right.get("input_tokens", 0)),
        "output_tokens": int(left.get("output_tokens", 0)) + int(right.get("output_tokens", 0)),
        "cached_tokens": int(left.get("cached_tokens", 0)) + int(right.get("cached_tokens", 0)),
        "total_tokens": int(left.get("total_tokens", 0)) + int(right.get("total_tokens", 0)),
        "cost_usd": round(float(left.get("cost_usd", 0)) + float(right.get("cost_usd", 0)), 8),
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
