from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from MiniClaw.llm.cancellation import CancellationToken
from MiniClaw.llm.openai_compatible import OpenAICompatibleClient, OpenAICompatibleRoute
from MiniClaw.llm.types import ChatMessage, ModelProfile, ModelRequest
from MiniClaw.trace.model_client import TracingModelClient, reset_active_run, set_active_run
from MiniClaw.trace.store import TraceRecorder, read_trace_records


class DelayedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[tuple[float, bytes]], failure: Exception | None = None) -> None:
        self.chunks = chunks
        self.failure = failure

    async def __aiter__(self):
        for delay, chunk in self.chunks:
            if delay:
                await asyncio.sleep(delay)
            yield chunk
        if self.failure is not None:
            raise self.failure

    async def aclose(self) -> None:
        return None


def sse(value) -> bytes:
    payload = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return f"data: {payload}\n\n".encode("utf-8")


def success_stream(content: str = "OK", *, usage: tuple[int, int] = (0, 0)) -> DelayedStream:
    return DelayedStream(
        [
            (0, sse({"choices": [{"index": 0, "delta": {"content": content}}]})),
            (
                0,
                sse(
                    {
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        "usage": {
                            "prompt_tokens": usage[0],
                            "completion_tokens": usage[1],
                        },
                    }
                ),
            ),
            (0, sse("[DONE]")),
        ]
    )


def request(*, token: CancellationToken | None = None) -> ModelRequest:
    return ModelRequest(
        profile=ModelProfile("primary-model", max_output_tokens=128),
        messages=[ChatMessage(role="user", content="test")],
        tools=[
            {
                "name": "write",
                "description": "write",
                "parameters": {"type": "object"},
            }
        ],
        cancellation_token=token,
    )


class OpenAIStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_deepseek_requests_explicitly_disable_thinking(self) -> None:
        captured: dict[str, object] = {}

        async def handler(wire_request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(wire_request.content))
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=success_stream(),
            )

        client = OpenAICompatibleClient(
            "secret",
            "https://api.deepseek.test/v1",
            provider="deepseek",
            transport=httpx.MockTransport(handler),
        )
        _ = [event async for event in client.stream(request())]
        self.assertEqual(captured["thinking"], {"type": "disabled"})

    async def test_text_deltas_and_fragmented_tool_arguments_are_streamed_and_joined(self) -> None:
        captured: dict[str, object] = {}

        async def handler(wire_request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(wire_request.content))
            stream = DelayedStream(
                [
                    (0, sse({"choices": [{"index": 0, "delta": {"role": "assistant"}}]})),
                    (0, sse({"choices": [{"index": 0, "delta": {"content": "Hel"}}]})),
                    (0, sse({"choices": [{"index": 0, "delta": {"content": "lo"}}]})),
                    (
                        0,
                        sse(
                            {
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {
                                            "tool_calls": [
                                                {
                                                    "index": 0,
                                                    "id": "call_",
                                                    "function": {
                                                        "name": "wr",
                                                        "arguments": '{"path":"a',
                                                    },
                                                }
                                            ]
                                        },
                                    }
                                ]
                            }
                        ),
                    ),
                    (
                        0,
                        sse(
                            {
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {
                                            "tool_calls": [
                                                {
                                                    "index": 0,
                                                    "id": "1",
                                                    "function": {
                                                        "name": "ite",
                                                        "arguments": '.txt","content":"OK"}',
                                                    },
                                                }
                                            ]
                                        },
                                        "finish_reason": "tool_calls",
                                    }
                                ],
                                "usage": {
                                    "prompt_tokens": 10,
                                    "completion_tokens": 4,
                                    "prompt_tokens_details": {"cached_tokens": 2},
                                },
                            }
                        ),
                    ),
                    (0, sse("[DONE]")),
                ]
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=stream,
            )

        client = OpenAICompatibleClient(
            "secret",
            "https://primary.test/v1",
            max_retries=0,
            retry_jitter_ratio=0,
            transport=httpx.MockTransport(handler),
        )
        events = [event async for event in client.stream(request())]
        self.assertTrue(captured["stream"])
        self.assertEqual(captured["stream_options"], {"include_usage": True})
        self.assertEqual([event.text for event in events if event.type == "text_delta"], ["Hel", "lo"])
        tool = next(event.tool_call for event in events if event.type == "tool_call")
        self.assertEqual(tool.call_id, "call_1")
        self.assertEqual(tool.name, "write")
        self.assertEqual(tool.arguments, {"path": "a.txt", "content": "OK"})
        reply = next(event.reply for event in events if event.type == "completed")
        self.assertEqual(reply.content, "Hello")
        self.assertEqual(reply.stop_reason, "tool_calls")
        self.assertEqual(reply.usage.cached_tokens, 2)
        self.assertIsNotNone(reply.metadata["time_to_first_token_ms"])

    async def test_first_token_timeout_fails_before_any_visible_output(self) -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=DelayedStream([(0.05, sse("[DONE]"))]),
            )

        client = OpenAICompatibleClient(
            "secret",
            "https://primary.test/v1",
            timeout_seconds=1,
            connect_timeout_seconds=1,
            first_token_timeout_seconds=0.01,
            idle_timeout_seconds=1,
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
        events = [event async for event in client.stream(request())]
        error = next(event.error for event in events if event.type == "error")
        self.assertIn("MODEL_FIRST_TOKEN_TIMEOUT", error)
        self.assertFalse(any(event.type == "text_delta" for event in events))

    async def test_slow_response_headers_use_first_token_not_connect_timeout(self) -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.03)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=success_stream("ready"),
            )

        client = OpenAICompatibleClient(
            "secret",
            "https://primary.test/v1",
            timeout_seconds=1,
            connect_timeout_seconds=0.01,
            first_token_timeout_seconds=0.1,
            idle_timeout_seconds=1,
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
        events = [event async for event in client.stream(request())]
        self.assertEqual(
            [event.text for event in events if event.type == "text_delta"],
            ["ready"],
        )
        self.assertFalse(any(event.type == "error" for event in events))

    async def test_idle_timeout_does_not_retry_after_text_was_emitted(self) -> None:
        calls = 0

        async def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=DelayedStream(
                    [
                        (0, sse({"choices": [{"index": 0, "delta": {"content": "partial"}}]})),
                        (0.05, sse("[DONE]")),
                    ]
                ),
            )

        client = OpenAICompatibleClient(
            "secret",
            "https://primary.test/v1",
            timeout_seconds=1,
            connect_timeout_seconds=1,
            first_token_timeout_seconds=1,
            idle_timeout_seconds=0.01,
            max_retries=2,
            retry_base_seconds=0.001,
            retry_max_seconds=0.001,
            retry_jitter_ratio=0,
            transport=httpx.MockTransport(handler),
        )
        events = [event async for event in client.stream(request())]
        self.assertEqual(calls, 1)
        self.assertEqual([event.text for event in events if event.type == "text_delta"], ["partial"])
        error = next(event.error for event in events if event.type == "error")
        self.assertIn("MODEL_IDLE_TIMEOUT", error)

    async def test_total_timeout_is_distinct_from_idle_timeout(self) -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=DelayedStream(
                    [
                        (0, sse({"choices": [{"index": 0, "delta": {"content": "one"}}]})),
                        (0.01, sse({"choices": [{"index": 0, "delta": {"content": "two"}}]})),
                        (0.02, sse("[DONE]")),
                    ]
                ),
            )

        client = OpenAICompatibleClient(
            "secret",
            "https://primary.test/v1",
            timeout_seconds=0.02,
            connect_timeout_seconds=0.02,
            first_token_timeout_seconds=0.02,
            idle_timeout_seconds=0.02,
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )
        events = [event async for event in client.stream(request())]
        error = next(event.error for event in events if event.type == "error")
        self.assertIn("MODEL_TOTAL_TIMEOUT", error)

    async def test_cancellation_token_aborts_an_inflight_stream(self) -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=DelayedStream([(1, sse("[DONE]"))]),
            )

        token = CancellationToken()
        client = OpenAICompatibleClient(
            "secret",
            "https://primary.test/v1",
            timeout_seconds=2,
            connect_timeout_seconds=1,
            first_token_timeout_seconds=1,
            idle_timeout_seconds=1,
            max_retries=0,
            transport=httpx.MockTransport(handler),
        )

        async def collect():
            return [event async for event in client.stream(request(token=token))]

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.01)
        token.cancel("test cancel")
        events = await asyncio.wait_for(task, timeout=1)
        reply = next(event.reply for event in events if event.type == "completed")
        self.assertEqual(reply.stop_reason, "aborted")
        self.assertFalse(any(event.type == "error" for event in events))
        self.assertIn("cancelled", [event.details.get("phase") for event in events if event.type == "transport"])

    async def test_cancellation_also_interrupts_retry_backoff(self) -> None:
        backoff_entered = asyncio.Event()

        class BackoffObservedClient(OpenAICompatibleClient):
            def _retry_delay(
                self, retry_index: int, retry_after_seconds: float | None
            ) -> float:
                backoff_entered.set()
                return super()._retry_delay(retry_index, retry_after_seconds)

        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="busy")

        token = CancellationToken()
        client = BackoffObservedClient(
            "secret",
            "https://primary.test/v1",
            max_retries=2,
            retry_base_seconds=1,
            retry_max_seconds=1,
            retry_jitter_ratio=0,
            transport=httpx.MockTransport(handler),
        )

        async def collect():
            return [event async for event in client.stream(request(token=token))]

        task = asyncio.create_task(collect())
        await asyncio.wait_for(backoff_entered.wait(), timeout=1)
        await asyncio.sleep(0)
        token.cancel("stop retries")
        events = await asyncio.wait_for(task, timeout=1)
        reply = next(event.reply for event in events if event.type == "completed")
        self.assertEqual(reply.stop_reason, "aborted")
        cancelled = next(
            event.details for event in events if event.type == "transport" and event.details.get("phase") == "cancelled"
        )
        self.assertEqual(cancelled["during"], "retry_backoff")

    async def test_visible_text_prevents_retry_and_provider_fallback(self) -> None:
        calls: list[str] = []

        async def handler(wire_request: httpx.Request) -> httpx.Response:
            host = wire_request.url.host or ""
            calls.append(host)
            if host == "primary.test":
                return httpx.Response(
                    200,
                    request=wire_request,
                    headers={"content-type": "text/event-stream"},
                    stream=DelayedStream(
                        [
                            (
                                0,
                                sse(
                                    {
                                        "choices": [
                                            {"index": 0, "delta": {"content": "visible"}}
                                        ]
                                    }
                                ),
                            )
                        ],
                        httpx.ReadError("connection lost", request=wire_request),
                    ),
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=success_stream("duplicate"),
            )

        client = OpenAICompatibleClient(
            "primary-secret",
            "https://primary.test/v1",
            max_retries=2,
            retry_base_seconds=0.001,
            retry_max_seconds=0.001,
            retry_jitter_ratio=0,
            fallback_routes=(
                OpenAICompatibleRoute(
                    provider="backup",
                    api_key="backup-secret",
                    base_url="https://backup.test/v1",
                    model_id="backup-model",
                ),
            ),
            transport=httpx.MockTransport(handler),
        )

        events = [event async for event in client.stream(request())]
        self.assertEqual(calls, ["primary.test"])
        self.assertEqual(
            [event.text for event in events if event.type == "text_delta"], ["visible"]
        )
        self.assertFalse(
            any(
                event.type == "transport"
                and event.details.get("phase") in {"retry_scheduled", "fallback_selected"}
                for event in events
            )
        )

    async def test_429_retries_then_succeeds_without_duplicate_completion(self) -> None:
        calls = 0

        async def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(429, text="busy", headers={"Retry-After": "0"})
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=success_stream("done", usage=(100, 20)),
            )

        client = OpenAICompatibleClient(
            "secret",
            "https://primary.test/v1",
            max_retries=1,
            retry_base_seconds=0.001,
            retry_max_seconds=0.001,
            retry_jitter_ratio=0,
            transport=httpx.MockTransport(handler),
        )
        events = [event async for event in client.stream(request())]
        phases = [event.details.get("phase") for event in events if event.type == "transport"]
        self.assertEqual(calls, 2)
        self.assertIn("retry_scheduled", phases)
        self.assertEqual(sum(event.type == "completed" for event in events), 1)
        reply = next(event.reply for event in events if event.type == "completed")
        self.assertEqual(reply.usage.input_tokens, 100)

    async def test_network_disconnect_before_output_is_retried(self) -> None:
        calls = 0

        async def handler(wire_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(
                    200,
                    request=wire_request,
                    headers={"content-type": "text/event-stream"},
                    stream=DelayedStream([], httpx.ReadError("connection lost", request=wire_request)),
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=success_stream("recovered"),
            )

        client = OpenAICompatibleClient(
            "secret",
            "https://primary.test/v1",
            max_retries=1,
            retry_base_seconds=0.001,
            retry_max_seconds=0.001,
            retry_jitter_ratio=0,
            transport=httpx.MockTransport(handler),
        )
        events = [event async for event in client.stream(request())]
        reply = next(event.reply for event in events if event.type == "completed")
        self.assertEqual(calls, 2)
        self.assertEqual(reply.content, "recovered")

    async def test_exhausted_primary_falls_back_to_another_provider_and_model(self) -> None:
        seen: list[tuple[str, str]] = []

        async def handler(wire_request: httpx.Request) -> httpx.Response:
            body = json.loads(wire_request.content)
            seen.append((wire_request.url.host or "", body["model"]))
            if wire_request.url.host == "primary.test":
                return httpx.Response(503, text="unavailable")
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=success_stream("fallback"),
            )

        client = OpenAICompatibleClient(
            "primary-key",
            "https://primary.test/v1",
            provider="primary",
            max_retries=0,
            fallback_routes=(
                OpenAICompatibleRoute(
                    "deepseek",
                    "fallback-key",
                    "https://fallback.test/v1",
                    "fallback-model",
                ),
            ),
            transport=httpx.MockTransport(handler),
        )
        events = [event async for event in client.stream(request())]
        reply = next(event.reply for event in events if event.type == "completed")
        self.assertEqual(seen, [("primary.test", "primary-model"), ("fallback.test", "fallback-model")])
        self.assertEqual(reply.content, "fallback")
        self.assertEqual(reply.metadata["provider"], "deepseek")
        self.assertEqual(reply.metadata["model"], "fallback-model")

    async def test_trace_records_attempts_but_costs_only_the_successful_reply(self) -> None:
        calls = 0

        async def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(500, text="temporary")
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=success_stream("done", usage=(100, 20)),
            )

        with tempfile.TemporaryDirectory() as directory:
            recorder = TraceRecorder(Path(directory), "test", "stream")
            inner = OpenAICompatibleClient(
                "secret",
                "https://primary.test/v1",
                provider="primary",
                max_retries=1,
                retry_base_seconds=0.001,
                retry_max_seconds=0.001,
                retry_jitter_ratio=0,
                transport=httpx.MockTransport(handler),
            )
            traced = TracingModelClient(inner, recorder, provider="primary")
            run_id = recorder.new_run_id()
            context = set_active_run(run_id)
            try:
                profile = ModelProfile(
                    "primary-model",
                    max_output_tokens=128,
                    input_cost_per_million=1,
                    output_cost_per_million=2,
                )
                model_request = request()
                model_request.profile = profile
                _ = [event async for event in traced.stream(model_request)]
            finally:
                reset_active_run(context)
            records = read_trace_records(Path(directory, "trace.jsonl"))
            requests = [record for record in records if record["type"] == "model.request"]
            transports = [record for record in records if record["type"] == "model.transport"]
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]["data"]["attempts"], 2)
            self.assertEqual(requests[0]["data"]["retries"], 1)
            self.assertAlmostEqual(requests[0]["data"]["usage"]["cost_usd"], 0.00014)
            self.assertEqual(
                sum(record["data"].get("phase") == "attempt_started" for record in transports),
                2,
            )


if __name__ == "__main__":
    unittest.main()
