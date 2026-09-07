from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

import httpx

from MiniClaw.cancellation import CancellationToken, ModelCancelledError, OperationCancelledError
from .types import (
    AssistantReply,
    ChatMessage,
    ModelEvent,
    ModelRequest,
    TokenUsage,
    ToolInvocation,
)


@dataclass(slots=True, frozen=True)
class OpenAICompatibleRoute:
    provider: str
    api_key: str
    base_url: str
    model_id: str | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    cached_input_cost_per_million: float | None = None


@dataclass(slots=True)
class _ToolCallBuffer:
    call_id: str = ""
    name: str = ""
    argument_parts: list[str] = field(default_factory=list)


class _TransportFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        partial_reply: AssistantReply | None = None,
        emitted_output: bool = False,
    ) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.partial_reply = partial_reply
        self.emitted_output = emitted_output


class _SSEDecoder:
    def __init__(self) -> None:
        self._data: list[str] = []

    def feed(self, line: str) -> list[str]:
        if line == "":
            return self._flush()
        if line.startswith(":"):
            return []
        field, separator, value = line.partition(":")
        if separator and field == "data":
            self._data.append(value[1:] if value.startswith(" ") else value)
        return []

    def finish(self) -> list[str]:
        return self._flush()

    def _flush(self) -> list[str]:
        if not self._data:
            return []
        value = "\n".join(self._data)
        self._data.clear()
        return [value]


class _StreamAccumulator:
    def __init__(self, started: float) -> None:
        self.started = started
        self.content_parts: list[str] = []
        self.tool_calls: dict[int, _ToolCallBuffer] = {}
        self.usage = TokenUsage()
        self.finish_reason: str | None = None
        self.first_meaningful_at: float | None = None
        self.usage_reported = False
        self.cache_usage_reported = False

    def consume(self, payload: str) -> tuple[list[ModelEvent], bool]:
        if payload == "[DONE]":
            return [], True
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise _TransportFailure(
                "MODEL_SSE_INVALID_JSON",
                f"invalid SSE JSON payload: {payload[:500]}",
                retryable=False,
                partial_reply=self.partial_reply(),
            ) from exc
        if not isinstance(value, dict):
            raise _TransportFailure(
                "MODEL_SSE_INVALID_EVENT",
                "SSE data must decode to an object",
                retryable=False,
                partial_reply=self.partial_reply(),
            )
        if value.get("error"):
            error = value["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise _TransportFailure(
                "MODEL_PROVIDER_ERROR",
                str(message or "provider returned an SSE error"),
                retryable=True,
                partial_reply=self.partial_reply(),
            )

        events: list[ModelEvent] = []
        meaningful = False
        for choice in value.get("choices") or []:
            if not isinstance(choice, dict) or int(choice.get("index") or 0) != 0:
                continue
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                delta = {}
            content = _content_text(delta.get("content"))
            if content:
                self.content_parts.append(content)
                events.append(ModelEvent(type="text_delta", text=content))
                meaningful = True
            if delta.get("reasoning_content"):
                meaningful = True
            for fallback_index, raw_call in enumerate(delta.get("tool_calls") or []):
                if not isinstance(raw_call, dict):
                    continue
                index = int(raw_call.get("index", fallback_index))
                buffer = self.tool_calls.setdefault(index, _ToolCallBuffer())
                if raw_call.get("id"):
                    buffer.call_id += str(raw_call["id"])
                function = raw_call.get("function") or {}
                if isinstance(function, dict):
                    if function.get("name"):
                        buffer.name += str(function["name"])
                    arguments = function.get("arguments")
                    if arguments is not None:
                        buffer.argument_parts.append(
                            arguments if isinstance(arguments, str) else json.dumps(arguments)
                        )
                meaningful = True
            legacy_function = delta.get("function_call")
            if isinstance(legacy_function, dict):
                buffer = self.tool_calls.setdefault(0, _ToolCallBuffer(call_id="call_0"))
                if legacy_function.get("name"):
                    buffer.name += str(legacy_function["name"])
                if legacy_function.get("arguments") is not None:
                    buffer.argument_parts.append(str(legacy_function["arguments"]))
                meaningful = True
            if choice.get("finish_reason") is not None:
                self.finish_reason = str(choice["finish_reason"])

        raw_usage = value.get("usage")
        if isinstance(raw_usage, dict):
            self.usage_reported = 'prompt_tokens' in raw_usage and 'completion_tokens' in raw_usage
            self.cache_usage_reported = 'cached_tokens' in (raw_usage.get('prompt_tokens_details') or {})
            self.usage = TokenUsage(
                input_tokens=int(raw_usage.get("prompt_tokens") or 0),
                output_tokens=int(raw_usage.get("completion_tokens") or 0),
                cached_tokens=int(
                    (raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
                ),
            )
            events.append(ModelEvent(type="usage", usage=self.usage))
        if meaningful and self.first_meaningful_at is None:
            self.first_meaningful_at = time.perf_counter()
        return events, False

    def final_reply(self, route: OpenAICompatibleRoute, model_id: str, attempt: int) -> AssistantReply:
        calls: list[ToolInvocation] = []
        for index in sorted(self.tool_calls):
            buffer = self.tool_calls[index]
            raw_arguments = "".join(buffer.argument_parts) or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise _TransportFailure(
                    "MODEL_TOOL_ARGUMENTS_INVALID",
                    f"invalid tool arguments for {buffer.name or index}: {raw_arguments[:1000]}",
                    retryable=False,
                    partial_reply=self.partial_reply(),
                ) from exc
            if not isinstance(arguments, dict):
                raise _TransportFailure(
                    "MODEL_TOOL_ARGUMENTS_INVALID",
                    "tool arguments must decode to an object",
                    retryable=False,
                    partial_reply=self.partial_reply(),
                )
            calls.append(
                ToolInvocation(
                    call_id=buffer.call_id or f"call_{index}",
                    name=buffer.name,
                    arguments=arguments,
                )
            )
        stop_reason = "tool_calls" if calls else "length" if self.finish_reason == "length" else "stop"
        first_token_ms = (
            round((self.first_meaningful_at - self.started) * 1000)
            if self.first_meaningful_at is not None
            else None
        )
        return AssistantReply(
            content="".join(self.content_parts),
            tool_calls=calls,
            stop_reason=stop_reason,
            usage=self.usage,
            metadata={
                "provider": route.provider,
                "model": model_id,
                "attempt": attempt,
                "usage_reported": self.usage_reported,
                "cache_usage_reported": self.cache_usage_reported,
                "time_to_first_token_ms": first_token_ms,
                "input_cost_per_million": route.input_cost_per_million,
                "output_cost_per_million": route.output_cost_per_million,
                "cached_input_cost_per_million": route.cached_input_cost_per_million,
            },
        )

    def partial_reply(self) -> AssistantReply:
        return AssistantReply(content="".join(self.content_parts), stop_reason="error", usage=self.usage)


@dataclass(slots=True)
class OpenAICompatibleClient:
    """Native SSE OpenAI-compatible transport with cancellation, retry, and fallback."""

    api_key: str
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 120.0
    provider: str = "openai-compatible"
    model_id: str | None = None
    connect_timeout_seconds: float = 10.0
    first_token_timeout_seconds: float = 60.0
    idle_timeout_seconds: float = 30.0
    max_retries: int = 2
    retry_base_seconds: float = 0.5
    retry_max_seconds: float = 8.0
    retry_jitter_ratio: float = 0.2
    fallback_routes: tuple[OpenAICompatibleRoute, ...] = ()
    include_usage: bool = True
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        primary = OpenAICompatibleRoute(
            provider=self.provider,
            api_key=self.api_key,
            base_url=self.base_url,
            model_id=self.model_id,
            input_cost_per_million=request.profile.input_cost_per_million,
            output_cost_per_million=request.profile.output_cost_per_million,
            cached_input_cost_per_million=request.profile.cached_input_cost_per_million,
        )
        routes = (
            primary,
            *tuple(
                OpenAICompatibleRoute(
                    provider=route.provider,
                    api_key=route.api_key,
                    base_url=route.base_url,
                    model_id=route.model_id,
                    input_cost_per_million=(
                        0.0 if route.input_cost_per_million is None else route.input_cost_per_million
                    ),
                    output_cost_per_million=(
                        0.0 if route.output_cost_per_million is None else route.output_cost_per_million
                    ),
                    cached_input_cost_per_million=(
                        0.0
                        if route.cached_input_cost_per_million is None
                        else route.cached_input_cost_per_million
                    ),
                )
                for route in self.fallback_routes
            ),
        )
        token = request.cancellation_token
        attempt_number = 0
        last_failure: _TransportFailure | None = None
        partial_reply: AssistantReply | None = None
        emitted_output = False
        buffer_output = request.metadata.get("buffer_network_retries") is True

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self.connect_timeout_seconds,
                read=None,
                write=self.connect_timeout_seconds,
                pool=self.connect_timeout_seconds,
            ),
            transport=self.transport,
            follow_redirects=False,
        ) as client:
            for route_index, route in enumerate(routes):
                model_id = route.model_id or request.profile.model_id
                if route_index:
                    yield ModelEvent(
                        type="transport",
                        details={
                            "phase": "fallback_selected",
                            "route_index": route_index,
                            "provider": route.provider,
                            "model": model_id,
                            "previous_error": str(last_failure) if last_failure else None,
                        },
                    )
                for retry_index in range(self.max_retries + 1):
                    attempt_number += 1
                    yield ModelEvent(
                        type="transport",
                        details={
                            "phase": "attempt_started",
                            "attempt": attempt_number,
                            "route_index": route_index,
                            "retry_index": retry_index,
                            "provider": route.provider,
                            "model": model_id,
                        },
                    )
                    completed_event: ModelEvent | None = None
                    buffered_events: list[ModelEvent] = []
                    try:
                        async for event in self._stream_attempt(
                            client, request, route, model_id, attempt_number, token
                        ):
                            if event.type == "completed":
                                completed_event = event
                            else:
                                if buffer_output and event.type in {"text_delta", "tool_call", "usage"}:
                                    buffered_events.append(event)
                                    continue
                                if event.type in {"text_delta", "tool_call"}:
                                    emitted_output = True
                                yield event
                        if completed_event is None:
                            raise _TransportFailure(
                                "MODEL_STREAM_INCOMPLETE",
                                "SSE stream ended without a completed reply",
                                retryable=True,
                                emitted_output=emitted_output,
                            )
                        yield ModelEvent(
                            type="transport",
                            details={
                                "phase": "attempt_succeeded",
                                "attempt": attempt_number,
                                "route_index": route_index,
                                "retry_index": retry_index,
                                "provider": route.provider,
                                "model": model_id,
                            },
                        )
                        for buffered_event in buffered_events:
                            yield buffered_event
                        yield completed_event
                        return
                    except ModelCancelledError as exc:
                        partial_reply = exc.partial_reply or partial_reply
                        yield ModelEvent(
                            type="transport",
                            details={
                                "phase": "cancelled",
                                "attempt": attempt_number,
                                "provider": route.provider,
                                "model": model_id,
                                "reason": str(exc),
                                "during": "stream_attempt",
                            },
                        )
                        yield ModelEvent(
                            type="completed",
                            reply=AssistantReply(
                                content=partial_reply.content if partial_reply else "",
                                stop_reason="aborted",
                                usage=partial_reply.usage if partial_reply else TokenUsage(),
                                metadata={
                                    "provider": route.provider,
                                    "model": model_id,
                                    "attempt": attempt_number,
                                },
                            ),
                        )
                        return
                    except _TransportFailure as exc:
                        last_failure = exc
                        partial_reply = exc.partial_reply or partial_reply
                        emitted_output = emitted_output or (not buffer_output and (
                            exc.emitted_output or bool(partial_reply and partial_reply.content)
                        ))
                    except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                        last_failure = _TransportFailure(
                            "MODEL_NETWORK_ERROR",
                            f"{type(exc).__name__}: {exc}",
                            retryable=True,
                            partial_reply=partial_reply,
                            emitted_output=emitted_output,
                        )
                    except Exception as exc:
                        last_failure = _TransportFailure(
                            "MODEL_TRANSPORT_ERROR",
                            f"{type(exc).__name__}: {exc}",
                            retryable=False,
                            partial_reply=partial_reply,
                            emitted_output=emitted_output,
                        )

                    assert last_failure is not None
                    yield ModelEvent(
                        type="transport",
                        details={
                            "phase": "attempt_failed",
                            "attempt": attempt_number,
                            "route_index": route_index,
                            "retry_index": retry_index,
                            "provider": route.provider,
                            "model": model_id,
                            "error_code": last_failure.code,
                            "error": str(last_failure),
                            "status_code": last_failure.status_code,
                            "retryable": last_failure.retryable,
                            "emitted_output": emitted_output,
                            "buffered_output_discarded": bool(buffered_events),
                            "tools_executed_in_request": False,
                        },
                    )
                    if emitted_output:
                        break
                    if last_failure.retryable and retry_index < self.max_retries:
                        delay = self._retry_delay(retry_index, last_failure.retry_after_seconds)
                        yield ModelEvent(
                            type="transport",
                            details={
                                "phase": "retry_scheduled",
                                "attempt": attempt_number,
                                "next_attempt": attempt_number + 1,
                                "provider": route.provider,
                                "model": model_id,
                                "delay_ms": round(delay * 1000),
                                "error_code": last_failure.code,
                            },
                        )
                        try:
                            await _sleep_cancelable(delay, token)
                        except ModelCancelledError as exc:
                            yield ModelEvent(
                                type="transport",
                                details={
                                    "phase": "cancelled",
                                    "attempt": attempt_number,
                                    "provider": route.provider,
                                    "model": model_id,
                                    "reason": str(exc),
                                    "during": "retry_backoff",
                                },
                            )
                            yield ModelEvent(
                                type="completed",
                                reply=AssistantReply(
                                    content=partial_reply.content if partial_reply else "",
                                    stop_reason="aborted",
                                    usage=partial_reply.usage if partial_reply else TokenUsage(),
                                    metadata={
                                        "provider": route.provider,
                                        "model": model_id,
                                        "attempt": attempt_number,
                                    },
                                ),
                            )
                            return
                        continue
                    break
                if emitted_output:
                    break

        failure = last_failure or _TransportFailure(
            "MODEL_TRANSPORT_ERROR", "model request failed", retryable=False
        )
        message = str(failure)
        yield ModelEvent(type="error", error=message)
        yield ModelEvent(
            type="completed",
            reply=AssistantReply(
                content=partial_reply.content if partial_reply else "",
                stop_reason="error",
                usage=partial_reply.usage if partial_reply else TokenUsage(),
                error=message,
                metadata={"attempts": attempt_number, "error_code": failure.code,
                          "retryable": failure.retryable, "emitted_output": emitted_output},
            ),
        )

    async def _stream_attempt(
        self,
        client: httpx.AsyncClient,
        request: ModelRequest,
        route: OpenAICompatibleRoute,
        model_id: str,
        attempt: int,
        token: CancellationToken | None,
    ) -> AsyncIterator[ModelEvent]:
        started = time.perf_counter()
        deadline = started + self.timeout_seconds
        accumulator = _StreamAccumulator(started)
        body: dict[str, Any] = {
            "model": model_id,
            "messages": [self._message_to_wire(message) for message in request.messages],
            "max_tokens": request.profile.max_output_tokens,
            "stream": True,
        }
        if route.provider == "deepseek":
            # DeepSeek's OpenAI-compatible switchable-thinking API otherwise may
            # spend the full completion budget on reasoning and emit no answer.
            body["thinking"] = {"type": "disabled"}
        if self.include_usage:
            body["stream_options"] = {"include_usage": True}
        if request.tools:
            body["tools"] = [{"type": "function", "function": tool} for tool in request.tools]
            body["tool_choice"] = "auto"
        if request.temperature is not None:
            body["temperature"] = request.temperature
        endpoint = f"{route.base_url.rstrip('/')}/chat/completions"
        wire_request = client.build_request(
            "POST",
            endpoint,
            json=body,
            headers={
                "Authorization": f"Bearer {route.api_key}",
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
            },
        )
        # httpx already applies ``connect_timeout_seconds`` to establishing the
        # socket (and to pool/write operations).  ``client.send`` also waits for
        # the server's response headers, so wrapping the whole call in the
        # connect timeout incorrectly treats a slow model startup as a failed
        # network connection.  Until headers arrive, the user-visible boundary
        # is the first-token deadline instead.
        header_timeout = min(self.first_token_timeout_seconds, self.timeout_seconds)
        if header_timeout == self.timeout_seconds:
            header_timeout_code = "MODEL_TOTAL_TIMEOUT"
            header_timeout_message = f"model request exceeded {self.timeout_seconds:g} seconds"
        else:
            header_timeout_code = "MODEL_FIRST_TOKEN_TIMEOUT"
            header_timeout_message = (
                f"model produced no token within {self.first_token_timeout_seconds:g} seconds"
            )
        response = await _await_cancelable(
            client.send(wire_request, stream=True),
            token,
            header_timeout,
            header_timeout_code,
            header_timeout_message,
        )
        try:
            yield ModelEvent(type="transport", details={
                "phase": "response_headers", "attempt": attempt,
                "provider_request_id": response.headers.get("x-request-id") or response.headers.get("request-id"),
                "status_code": response.status_code,
            })
            if response.status_code >= 400:
                detail = (
                    await _await_cancelable(
                        response.aread(),
                        token,
                        min(self.idle_timeout_seconds, max(0.001, deadline - time.perf_counter())),
                        "MODEL_IDLE_TIMEOUT",
                        "model error response body timed out",
                    )
                ).decode("utf-8", errors="replace")[:1000]
                raise _TransportFailure(
                    "MODEL_HTTP_ERROR",
                    f"model endpoint returned HTTP {response.status_code}: {detail}",
                    retryable=_retryable_status(response.status_code),
                    status_code=response.status_code,
                    retry_after_seconds=_retry_after_seconds(response.headers.get("Retry-After")),
                )
            content_type = response.headers.get("content-type", "").lower()
            if "text/event-stream" not in content_type:
                detail = (
                    await _await_cancelable(
                        response.aread(),
                        token,
                        min(self.idle_timeout_seconds, max(0.001, deadline - time.perf_counter())),
                        "MODEL_IDLE_TIMEOUT",
                        "non-SSE model response body timed out",
                    )
                ).decode("utf-8", errors="replace")[:1000]
                raise _TransportFailure(
                    "MODEL_STREAM_UNSUPPORTED",
                    f"expected text/event-stream, received {content_type or 'unknown'}: {detail}",
                    retryable=False,
                )

            iterator = response.aiter_lines().__aiter__()
            decoder = _SSEDecoder()
            done = False
            while not done:
                now = time.perf_counter()
                total_remaining = deadline - now
                if total_remaining <= 0:
                    raise _TransportFailure(
                        "MODEL_TOTAL_TIMEOUT",
                        f"model stream exceeded {self.timeout_seconds:g} seconds",
                        retryable=True,
                        partial_reply=accumulator.partial_reply(),
                        emitted_output=bool(accumulator.content_parts),
                    )
                if accumulator.first_meaningful_at is None:
                    first_remaining = self.first_token_timeout_seconds - (now - started)
                    timeout = min(first_remaining, total_remaining)
                    if total_remaining <= first_remaining:
                        timeout_code = "MODEL_TOTAL_TIMEOUT"
                        timeout_message = f"model stream exceeded {self.timeout_seconds:g} seconds"
                    else:
                        timeout_code = "MODEL_FIRST_TOKEN_TIMEOUT"
                        timeout_message = (
                            f"model produced no token within {self.first_token_timeout_seconds:g} seconds"
                        )
                else:
                    timeout = min(self.idle_timeout_seconds, total_remaining)
                    if total_remaining <= self.idle_timeout_seconds:
                        timeout_code = "MODEL_TOTAL_TIMEOUT"
                        timeout_message = f"model stream exceeded {self.timeout_seconds:g} seconds"
                    else:
                        timeout_code = "MODEL_IDLE_TIMEOUT"
                        timeout_message = (
                            f"model stream was idle for {self.idle_timeout_seconds:g} seconds"
                        )
                if timeout <= 0:
                    raise _TransportFailure(
                        timeout_code,
                        timeout_message,
                        retryable=True,
                        partial_reply=accumulator.partial_reply(),
                        emitted_output=bool(accumulator.content_parts),
                    )
                try:
                    line = await _await_cancelable(
                        iterator.__anext__(),
                        token,
                        timeout,
                        timeout_code,
                        timeout_message,
                    )
                except StopAsyncIteration:
                    for payload in decoder.finish():
                        events, payload_done = accumulator.consume(payload)
                        for event in events:
                            yield event
                        done = done or payload_done
                    break
                for payload in decoder.feed(line):
                    events, payload_done = accumulator.consume(payload)
                    for event in events:
                        yield event
                    done = done or payload_done

            if not done and accumulator.finish_reason is None:
                raise _TransportFailure(
                    "MODEL_STREAM_DISCONNECTED",
                    "SSE connection closed before a finish reason or [DONE] marker",
                    retryable=True,
                    partial_reply=accumulator.partial_reply(),
                    emitted_output=bool(accumulator.content_parts),
                )
            reply = accumulator.final_reply(route, model_id, attempt)
            for tool_call in reply.tool_calls:
                yield ModelEvent(type="tool_call", tool_call=tool_call)
            yield ModelEvent(type="completed", reply=reply)
        except ModelCancelledError as exc:
            exc.partial_reply = accumulator.partial_reply()
            raise
        except _TransportFailure as exc:
            if exc.partial_reply is None:
                exc.partial_reply = accumulator.partial_reply()
            exc.emitted_output = exc.emitted_output or bool(accumulator.content_parts)
            raise
        finally:
            await response.aclose()

    def _retry_delay(self, retry_index: int, retry_after_seconds: float | None) -> float:
        delay = min(self.retry_max_seconds, self.retry_base_seconds * (2**retry_index))
        if retry_after_seconds is not None:
            delay = min(self.retry_max_seconds, max(delay, retry_after_seconds))
        if self.retry_jitter_ratio:
            delay *= 1 + random.uniform(-self.retry_jitter_ratio, self.retry_jitter_ratio)
        return max(0.001, delay)

    @staticmethod
    def _message_to_wire(message: ChatMessage) -> dict[str, Any]:
        wire: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.role == "assistant" and message.tool_calls:
            wire["tool_calls"] = [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        if message.role == "tool":
            wire["tool_call_id"] = message.tool_call_id
            if message.name:
                wire["name"] = message.name
        return wire


T = TypeVar("T")


async def _await_cancelable(
    awaitable: Awaitable[T],
    token: CancellationToken | None,
    timeout: float,
    timeout_code: str,
    timeout_message: str,
) -> T:
    operation = asyncio.create_task(awaitable)
    if token is not None:
        try:
            token.raise_if_cancelled(stage="model_transport")
        except OperationCancelledError as exc:
            operation.cancel()
            with contextlib.suppress(BaseException):
                await operation
            raise ModelCancelledError(str(exc), stage=exc.stage) from exc
    cancellation = asyncio.create_task(token.wait()) if token is not None else None
    waiters = {operation}
    if cancellation is not None:
        waiters.add(cancellation)
    try:
        done, _ = await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if cancellation is not None and cancellation in done:
            operation.cancel()
            with contextlib.suppress(BaseException):
                await operation
            raise ModelCancelledError(token.reason)
        if operation in done:
            return operation.result()
        operation.cancel()
        with contextlib.suppress(BaseException):
            await operation
        raise _TransportFailure(timeout_code, timeout_message, retryable=True)
    finally:
        if cancellation is not None:
            cancellation.cancel()
            with contextlib.suppress(BaseException):
                await cancellation


async def _sleep_cancelable(delay: float, token: CancellationToken | None) -> None:
    await _await_cancelable(
        asyncio.sleep(delay),
        token,
        delay + 1,
        "MODEL_RETRY_SLEEP_TIMEOUT",
        "retry delay did not complete",
    )


def _retryable_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or 500 <= status_code <= 599


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            return max(0.0, parsed.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type") in {"text", "output_text"}:
            parts.append(str(item.get("text") or ""))
    return "".join(parts)
