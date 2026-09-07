from __future__ import annotations

import time
import uuid
import json
import math
from contextvars import ContextVar, Token
from dataclasses import asdict
from typing import Any

from MiniClaw.llm.client import ModelClient
from MiniClaw.llm.types import AssistantReply, ModelEvent, ModelRequest
from MiniClaw.agent.context import is_context_update

from .store import TraceRecorder, usage_dict, utc_now


_active_run_id: ContextVar[str | None] = ContextVar("miniclaw_trace_run_id", default=None)


_CHECKPOINT_MARKER = "[miniclaw working context checkpoint]"
_MEMORY_MARKERS = ("<retrieved_memory>", "miniclaw-managed-memory:", "## miniclaw managed semantic memory")


def _estimated_tokens(value: Any) -> int:
    """Cheap observability estimate; never present this as provider billing usage."""
    if value is None:
        return 0
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return math.ceil(len(text.encode("utf-8")) / 4) if text else 0


def _input_token_breakdown(request: ModelRequest) -> dict[str, Any]:
    """Assign every serialized request component to exactly one source bucket."""
    buckets = {
        "system": 0,
        "work_dialogue": 0,
        "memory_injection": 0,
        "checkpoint_summary": 0,
        "context_updates": 0,
        "tool_results": 0,
        "tool_call_arguments": 0,
        "tool_definitions": 0,
    }
    for message in request.messages:
        content = message.content or ""
        lowered = content.casefold()
        if is_context_update(message):
            bucket = 'context_updates'
        elif _CHECKPOINT_MARKER in lowered:
            bucket = "checkpoint_summary"
        elif any(marker in lowered for marker in _MEMORY_MARKERS):
            bucket = "memory_injection"
        elif message.role == "system":
            bucket = "system"
        elif message.role == "tool":
            bucket = "tool_results"
        else:
            bucket = "work_dialogue"
        # Keep message metadata with the message's one bucket so no component is
        # counted twice while still accounting for provider-visible tool IDs/names.
        buckets[bucket] += _estimated_tokens(
            {
                "role": message.role,
                "content": content,
                "name": message.name,
                "tool_call_id": message.tool_call_id,
            }
        )
        if message.tool_calls:
            buckets["tool_call_arguments"] += _estimated_tokens(
                [
                    {"call_id": call.call_id, "name": call.name, "arguments": call.arguments}
                    for call in message.tool_calls
                ]
            )
    buckets["tool_definitions"] = _estimated_tokens(request.tools)
    total = sum(buckets.values())
    return {
        "is_estimate": True,
        "method": "utf8_bytes_div_4_ceil",
        "total_tokens": total,
        "by_source": buckets,
    }


def _estimated_output(reply: AssistantReply | None) -> dict[str, Any]:
    content = _estimated_tokens(reply.content if reply else "")
    calls = _estimated_tokens(
        [
            {"call_id": call.call_id, "name": call.name, "arguments": call.arguments}
            for call in (reply.tool_calls if reply else [])
        ]
    )
    return {"is_estimate": True, "method": "utf8_bytes_div_4_ceil", "total_tokens": content + calls}


def set_active_run(run_id: str) -> Token[str | None]:
    return _active_run_id.set(run_id)


def reset_active_run(token: Token[str | None]) -> None:
    _active_run_id.reset(token)


class TracingModelClient:
    """Records every model boundary, including Agent, Goal Judge, and Compaction calls."""

    def __init__(
        self,
        inner: ModelClient,
        recorder: TraceRecorder,
        provider: str = "openai-compatible",
    ) -> None:
        self.inner = inner
        self.recorder = recorder
        self.provider = provider

    async def stream(self, request: ModelRequest):
        request_id = str(uuid.uuid4())
        run_id = _active_run_id.get()
        started_at = utc_now()
        started = time.perf_counter()
        reply: AssistantReply | None = None
        error_text: str | None = None
        transport_events: list[dict[str, Any]] = []
        try:
            async for event in self.inner.stream(request):
                if event.type == "error" and event.error:
                    error_text = event.error
                elif event.type == "transport":
                    details = {"request_id": request_id, **event.details}
                    transport_events.append(details)
                    self.recorder.record("model.transport", details, run_id=run_id)
                elif event.type == "completed":
                    reply = event.reply
                    if reply and reply.error:
                        error_text = reply.error
                yield event
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000)
            usage = reply.usage if reply else None
            reply_metadata = reply.metadata if reply else {}
            input_price = reply_metadata.get("input_cost_per_million")
            output_price = reply_metadata.get("output_cost_per_million")
            cached_price = reply_metadata.get("cached_input_cost_per_million")
            trace_usage = usage_dict(
                usage.input_tokens if usage else 0,
                usage.output_tokens if usage else 0,
                usage.cached_tokens if usage else 0,
                input_cost_per_million=(
                    float(input_price)
                    if isinstance(input_price, (int, float))
                    else request.profile.input_cost_per_million
                ),
                output_cost_per_million=(
                    float(output_price)
                    if isinstance(output_price, (int, float))
                    else request.profile.output_cost_per_million
                ),
                cached_input_cost_per_million=(
                    float(cached_price)
                    if isinstance(cached_price, (int, float))
                    else request.profile.cached_input_cost_per_million
                ),
            )
            estimated_input = _input_token_breakdown(request)
            estimated_output = _estimated_output(reply)
            status = (
                "cancelled"
                if reply and reply.stop_reason == "aborted"
                else "error"
                if error_text or (reply and reply.stop_reason == "error")
                else "success"
                if reply
                else "cancelled"
            )
            attempts = sum(item.get("phase") == "attempt_started" for item in transport_events)
            retries = sum(item.get("phase") == "retry_scheduled" for item in transport_events)
            fallbacks = sum(item.get("phase") == "fallback_selected" for item in transport_events)
            from MiniClaw.llm.recovery import is_network_error
            network_failures = sum(
                item.get("phase") == "attempt_failed" and is_network_error(item)
                for item in transport_events
            )
            self.recorder.record(
                "model.request",
                {
                    "request_id": request_id,
                    "purpose": request.metadata.get("purpose", "agent"),
                    "status": status,
                    "provider": reply_metadata.get(
                        "provider", request.metadata.get("provider", self.provider)
                    ),
                    "model": reply_metadata.get("model", request.profile.model_id),
                    "started_at": started_at,
                    "completed_at": utc_now(),
                    "duration_ms": duration_ms,
                    "time_to_first_token_ms": reply_metadata.get("time_to_first_token_ms"),
                    "attempts": attempts or (1 if reply else 0),
                    "retries": retries,
                    "fallbacks": fallbacks,
                    "network_failures": network_failures,
                    "network_recovery": (
                        "recovered" if network_failures and status == "success"
                        else "unrecovered" if status == "error" and is_network_error({
                            "error": error_text, "error_code": reply_metadata.get("error_code")
                        }) else "none"
                    ),
                    "provider_request_ids": [item["provider_request_id"] for item in transport_events if item.get("provider_request_id")],
                    "error_code": reply_metadata.get("error_code"),
                    "stop_reason": reply.stop_reason if reply else status,
                    "error": error_text,
                    "usage": trace_usage,
                    "token_accounting": {
                        "provider_usage": {
                            "input_tokens": trace_usage["input_tokens"],
                            "output_tokens": trace_usage["output_tokens"],
                            "cached_tokens": trace_usage["cached_tokens"],
                            "is_estimate": False,
                        },
                        "estimated_input": estimated_input,
                        "estimated_output": estimated_output,
                    },
                    "context": {
                        "profile": asdict(request.profile),
                        "messages": [asdict(message) for message in request.messages],
                        "tools": request.tools,
                        "temperature": request.temperature,
                        "metadata": request.metadata,
                        "estimated_input_tokens": estimated_input,
                    },
                    "output": asdict(reply) if reply else None,
                },
                run_id=run_id,
            )
