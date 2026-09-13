from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from MiniClaw.cancellation import CancellationToken
from .types import AssistantReply, ChatMessage, ModelEvent, ModelRequest, TokenUsage, ToolInvocation


@dataclass(slots=True)
class AnthropicClient:
    """Native Anthropic Messages streaming client.

    Tool-call JSON is kept private until ``message_stop`` so a broken stream
    can never expose an executable half-call to AgentLoop.
    """

    api_key: str
    base_url: str = "https://api.anthropic.com"
    model_id: str | None = None
    timeout_seconds: float = 120.0
    max_retries: int = 5
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 32.0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        system, messages = self._messages(request.messages)
        body: dict[str, Any] = {
            "model": self.model_id or request.profile.model_id,
            "max_tokens": request.profile.max_output_tokens,
            "messages": messages,
            "stream": True,
        }
        if system:
            body["system"] = system
        if request.tools:
            body["tools"] = [
                {
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters", tool.get("input_schema", {"type": "object"})),
                }
                for tool in request.tools
            ]
        if request.temperature is not None:
            body["temperature"] = request.temperature

        token = request.cancellation_token
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds), follow_redirects=False) as client:
            for retry_index in range(self.max_retries + 1):
                yield ModelEvent(type="transport", details={
                    "phase": "attempt_started", "retry_index": retry_index,
                    "provider": "anthropic", "model": body["model"],
                })
                try:
                    async with client.stream(
                        "POST",
                        f"{self.base_url.rstrip('/')}/v1/messages",
                        json=body,
                        headers={
                            "x-api-key": self.api_key,
                            "anthropic-version": "2023-06-01",
                            "accept": "text/event-stream",
                            "content-type": "application/json",
                        },
                    ) as response:
                        if response.status_code >= 400:
                            detail = (await response.aread()).decode("utf-8", errors="replace")[:1000]
                            if response.status_code not in {408, 409, 425, 429} and response.status_code < 500:
                                raise RuntimeError(f"Anthropic HTTP {response.status_code}: {detail}")
                            raise ConnectionError(f"Anthropic HTTP {response.status_code}: {detail}")
                        reply = await self._read_stream(response, token)
                    yield ModelEvent(type="transport", details={
                        "phase": "attempt_succeeded", "retry_index": retry_index,
                        "provider": "anthropic", "model": body["model"],
                    })
                    if reply.content:
                        yield ModelEvent(type="text_delta", text=reply.content)
                    for tool_call in reply.tool_calls:
                        yield ModelEvent(type="tool_call", tool_call=tool_call)
                    yield ModelEvent(type="completed", reply=reply)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if retry_index >= self.max_retries or not isinstance(exc, (httpx.HTTPError, ConnectionError, TimeoutError)):
                        yield ModelEvent(type="completed", reply=AssistantReply(
                            stop_reason="error", error=f"{type(exc).__name__}: {exc}"
                        ))
                        return
                    delay = min(self.retry_max_seconds, self.retry_base_seconds * (2 ** retry_index))
                    yield ModelEvent(type="transport", details={
                        "phase": "retry_scheduled", "retry_index": retry_index,
                        "delay_ms": round(delay * 1000), "provider": "anthropic",
                    })
                    await asyncio.sleep(delay)

    async def _read_stream(self, response: httpx.Response, token: CancellationToken | None) -> AssistantReply:
        content = ""
        tool_calls: dict[int, dict[str, Any]] = {}
        usage = TokenUsage()
        stop_reason = "stop"
        event_name = ""
        saw_message_stop = False
        async for line in response.aiter_lines():
            if token is not None and token.cancelled:
                return AssistantReply(content=content, stop_reason="aborted", usage=usage)
            if line.startswith("event:"):
                event_name = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if event_name == "message_start":
                usage.input_tokens = int((data.get("message") or {}).get("usage", {}).get("input_tokens") or 0)
            elif event_name == "content_block_start":
                block = data.get("content_block") or {}
                if block.get("type") == "tool_use":
                    tool_calls[int(data.get("index", len(tool_calls)))] = {
                        "id": block.get("id", ""), "name": block.get("name", ""), "json": ""
                    }
            elif event_name == "content_block_delta":
                delta = data.get("delta") or {}
                if delta.get("type") == "text_delta":
                    content += delta.get("text", "")
                elif delta.get("type") == "input_json_delta":
                    call = tool_calls.get(int(data.get("index", 0)))
                    if call is not None:
                        call["json"] += delta.get("partial_json", "")
            elif event_name == "message_delta":
                delta = data.get("delta") or {}
                stop_reason = "tool_calls" if delta.get("stop_reason") == "tool_use" else "stop"
                usage.output_tokens = int((data.get("usage") or {}).get("output_tokens") or 0)
            elif event_name == "message_stop":
                saw_message_stop = True
        if not saw_message_stop:
            raise ConnectionError("Anthropic SSE stream ended before message_stop")
        parsed_calls = []
        for call in tool_calls.values():
            parsed_calls.append(ToolInvocation(call["id"], call["name"], json.loads(call["json"] or "{}")))
        return AssistantReply(content=content, tool_calls=parsed_calls, stop_reason=stop_reason, usage=usage)

    @staticmethod
    def _messages(messages: list[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        output: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "system":
                system_parts.append(message.content)
                continue
            if message.role == "tool":
                content: Any = [{"type": "tool_result", "tool_use_id": message.tool_call_id, "content": message.content}]
                role = "user"
            elif message.role == "assistant" and message.tool_calls:
                content = ([{"type": "text", "text": message.content}] if message.content else []) + [
                    {"type": "tool_use", "id": call.call_id, "name": call.name, "input": call.arguments}
                    for call in message.tool_calls
                ]
                role = "assistant"
            else:
                content = message.content
                role = message.role
            output.append({"role": role, "content": content})
        return "\n\n".join(system_parts), output
