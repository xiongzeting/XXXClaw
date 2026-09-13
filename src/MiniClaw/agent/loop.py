from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator, Callable

from MiniClaw.llm.types import (
    AssistantReply,
    ChatMessage,
    ModelProfile,
    ModelRequest,
    ToolInvocation,
)
from MiniClaw.llm.client import ModelClient
from MiniClaw.cancellation import CancellationToken, ToolCancelledError

from .events import AgentEvent
from .types import AgentToolExecutor
from .context import ContextJournal, PrefixDiagnostics


ContextTransform = Callable[[list[ChatMessage]], list[ChatMessage]]
SystemPromptProvider = Callable[[], str]
ContextMessagesProvider = Callable[[], list[ChatMessage]]


class AgentLoop:
    """Small model/tool loop with no coding-specific policy."""

    def __init__(
        self,
        model_client: ModelClient,
        profile: ModelProfile,
        tool_executor: AgentToolExecutor,
        system_prompt: str = "",
        token_budget: int = 1_000_000,
        time_budget_seconds: float = 3_600.0,
        no_progress_limit: int = 6,
        transform_context: ContextTransform | None = None,
        system_prompt_provider: SystemPromptProvider | None = None,
        context_messages_provider: ContextMessagesProvider | None = None,
        final_guard: Callable[[], str | None] | None = None,
        pause_message_provider: Callable[[], str] | None = None,
        context_diagnostics_provider: Callable[[], dict] | None = None,
        final_response_guard: Callable[[str], str | None] | None = None,
        request_tools_provider: Callable[[], list[dict]] | None = None,
        context_updates_provider: Callable[[], dict] | None = None,
    ) -> None:
        self.model_client = model_client
        self.profile = profile
        self.tool_executor = tool_executor
        self.system_prompt = system_prompt
        if token_budget <= 0:
            raise ValueError("token_budget must be positive")
        if time_budget_seconds <= 0:
            raise ValueError("time_budget_seconds must be positive")
        if no_progress_limit <= 0:
            raise ValueError("no_progress_limit must be positive")
        self.token_budget = token_budget
        self.time_budget_seconds = time_budget_seconds
        self.no_progress_limit = no_progress_limit
        self.transform_context = transform_context
        self.system_prompt_provider = system_prompt_provider
        self.context_messages_provider = context_messages_provider
        self.final_guard = final_guard
        self.pause_message_provider = pause_message_provider
        self.context_diagnostics_provider = context_diagnostics_provider
        self.final_response_guard = final_response_guard
        self.request_tools_provider = request_tools_provider
        self.context_updates_provider = context_updates_provider
        # Context updates are deltas.  Rebase only on a real size boundary;
        # frequent full resets resend retrieved evidence and were a major
        # source of cumulative input tokens in long eval runs.
        self.context_journal = ContextJournal(max_updates=128, extra_budget_bytes=64_000)
        self.prefix_diagnostics = PrefixDiagnostics()
        self.messages: list[ChatMessage] = []
        self._running = False
        self._cancellation_token: CancellationToken | None = None
        self.pending_request: ModelRequest | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def cancellation_token(self) -> CancellationToken | None:
        return self._cancellation_token

    def cancel(self, reason: str = "Agent run cancelled by the user") -> bool:
        token = self._cancellation_token
        if not self._running or token is None:
            return False
        return token.cancel(reason)

    async def run(self, prompt: str | None, *, start_turn: int = 1) -> AsyncIterator[AgentEvent]:
        if self._running:
            raise RuntimeError("agent is already running")
        self._running = True
        cancellation_token = CancellationToken()
        self._cancellation_token = cancellation_token
        started = time.monotonic()
        used_tokens = 0
        no_progress_streak = 0
        last_progress_fingerprint: str | None = None
        try:
            yield AgentEvent(type="run_started")
            if prompt is not None:
                self.pending_request = None
                user_message = ChatMessage(role="user", content=prompt)
                self.messages.append(user_message)
                yield AgentEvent(type="message_added", message=user_message)

            turn_number = start_turn
            while True:
                if cancellation_token.cancelled:
                    yield AgentEvent(
                        type="run_finished",
                        details={
                            "stop_reason": "aborted",
                            "reason": cancellation_token.reason,
                        },
                    )
                    return
                elapsed = time.monotonic() - started
                if elapsed >= self.time_budget_seconds:
                    for event in self._budget_events("time", used_tokens, elapsed, no_progress_streak):
                        yield event
                    return
                if used_tokens >= self.token_budget:
                    for event in self._budget_events(
                        "token", used_tokens, elapsed, no_progress_streak
                    ):
                        yield event
                    return
                # A request-scoped advertisement must never leak into the
                # next request.  The executor will bind the freshly built
                # definition set immediately below.
                clear_request_tool_names = getattr(
                    self.tool_executor, "set_request_tool_names", None
                )
                if callable(clear_request_tool_names):
                    clear_request_tool_names(None)
                yield AgentEvent(type="turn_started", details={"turn": turn_number})
                reply: AssistantReply | None = None
                recovering = prompt is None and turn_number == start_turn and self.pending_request is not None
                system_prompt = None
                if not recovering and self.context_updates_provider:
                    # Refresh the source before taking its snapshot, then append after
                    # a complete tool batch. Persist updates through the ordinary log.
                    system_prompt = self.system_prompt_provider() if self.system_prompt_provider else self.system_prompt
                    for update in self.context_journal.update(self.messages, self.context_updates_provider()):
                        self.messages.append(update)
                        yield AgentEvent(type='message_added', message=update)
                request_messages = [] if recovering else self._request_messages(system_prompt=system_prompt)
                request_tools = (
                    self.request_tools_provider()
                    if self.request_tools_provider
                    else self.tool_executor.definitions()
                )
                request = ModelRequest(
                    profile=self.profile,
                    messages=request_messages,
                    tools=request_tools,
                    metadata={"purpose": "agent", "buffer_network_retries": True,
                              **({"context_projection": self.context_diagnostics_provider()}
                                 if self.context_diagnostics_provider else {})},
                    cancellation_token=cancellation_token,
                )
                if recovering:
                    request = self.pending_request
                    request.cancellation_token = cancellation_token
                set_request_tool_names = getattr(
                    self.tool_executor, "set_request_tool_names", None
                )
                if callable(set_request_tool_names):
                    set_request_tool_names(
                        definition.get("name")
                        for definition in (request.tools or [])
                        if isinstance(definition, dict) and definition.get("name")
                    )
                request.metadata['request_prefix'] = self.prefix_diagnostics.observe(request)
                request.metadata['context_updates'] = dict(self.context_journal.last_details)
                self.pending_request = request
                remaining = max(0.001, self.time_budget_seconds - (time.monotonic() - started))
                try:
                    async with asyncio.timeout(remaining):
                        async for model_event in self.model_client.stream(request):
                            if model_event.type == "text_delta":
                                if self.final_guard is None and self.final_response_guard is None:
                                    yield AgentEvent(type="text_delta", text=model_event.text)
                            elif model_event.type == "error":
                                yield AgentEvent(type="error", text=model_event.error or "model error", is_error=True)
                            elif model_event.type == "completed":
                                reply = model_event.reply
                except TimeoutError:
                    for event in self._budget_events(
                        "time", used_tokens, time.monotonic() - started, no_progress_streak
                    ):
                        yield event
                    return

                if reply is None:
                    raise RuntimeError("model stream ended without a completed reply")
                used_tokens += reply.usage.input_tokens + reply.usage.output_tokens
                if reply.stop_reason == "aborted" or cancellation_token.cancelled:
                    assistant_message = ChatMessage(role="assistant", content=reply.content)
                    if reply.content:
                        self.messages.append(assistant_message)
                        yield AgentEvent(type="message_added", message=assistant_message, usage=reply.usage)
                    yield AgentEvent(
                        type="run_finished",
                        message=assistant_message if reply.content else None,
                        usage=reply.usage,
                        details={"stop_reason": "aborted", "reason": cancellation_token.reason},
                    )
                    return
                if reply.error:
                    raise RuntimeError(reply.error)
                self.pending_request = None

                blocker = self.final_guard() if self.final_guard and not reply.tool_calls else None
                if not blocker and not reply.tool_calls and self.final_response_guard:
                    blocker = self.final_response_guard(reply.content)
                if blocker:
                    fingerprint = self._fingerprint(reply, blocker)
                    no_progress_streak = (
                        no_progress_streak + 1
                        if fingerprint == last_progress_fingerprint
                        else 0
                    )
                    last_progress_fingerprint = fingerprint
                    if no_progress_streak >= self.no_progress_limit:
                        for event in self._budget_events(
                            "no_progress", used_tokens, time.monotonic() - started,
                            no_progress_streak,
                        ):
                            yield event
                        return
                    feedback = ChatMessage(role="user", content="[COMPLETION_CHECK] " + blocker)
                    self.messages.append(feedback)
                    yield AgentEvent(type="message_added", message=feedback)
                    yield AgentEvent(type="turn_finished", usage=reply.usage)
                    continue
                if (self.final_guard is not None or self.final_response_guard is not None) and reply.content:
                    yield AgentEvent(type="text_delta", text=reply.content)

                assistant_message = ChatMessage(
                    role="assistant",
                    content=reply.content,
                    tool_calls=reply.tool_calls,
                )
                self.messages.append(assistant_message)
                yield AgentEvent(type="message_added", message=assistant_message, usage=reply.usage)

                if not reply.tool_calls:
                    yield AgentEvent(type="turn_finished", message=assistant_message, usage=reply.usage)
                    yield AgentEvent(
                        type="run_finished",
                        message=assistant_message,
                        usage=reply.usage,
                        details={"stop_reason": reply.stop_reason},
                    )
                    return

                call_index = 0
                while call_index < len(reply.tool_calls):
                    if cancellation_token.cancelled:
                        for cancelled_event in self._cancelled_tool_events(
                            reply.tool_calls[call_index:],
                            cancellation_token.reason,
                        ):
                            yield cancelled_event
                        yield AgentEvent(
                            type="run_finished",
                            usage=reply.usage,
                            details={"stop_reason": "aborted", "reason": cancellation_token.reason},
                        )
                        return
                    # Only explicitly read-only calls are safe to overlap. The
                    # executor remains the sole dispatch boundary; this merely
                    # schedules independent calls and reassembles their results
                    # in the model's original order.
                    parallel_safe = getattr(self.tool_executor, "is_parallel_safe", None)
                    if callable(parallel_safe) and parallel_safe(reply.tool_calls[call_index].name):
                        end = call_index + 1
                        while end < len(reply.tool_calls) and parallel_safe(reply.tool_calls[end].name):
                            end += 1
                    else:
                        end = call_index + 1
                    batch = reply.tool_calls[call_index:end]
                    is_available = getattr(self.tool_executor, "is_available", None)
                    for batch_call in batch:
                        tool_is_available = bool(is_available(batch_call.name)) if callable(is_available) else True
                        if tool_is_available:
                            yield AgentEvent(type="tool_started", tool_call=batch_call)
                    remaining = max(0.001, self.time_budget_seconds - (time.monotonic() - started))
                    try:
                        async with asyncio.timeout(remaining):
                            results = await asyncio.gather(*(
                                self.tool_executor.execute(batch_call, cancellation_token)
                                for batch_call in batch
                            ))
                    except TimeoutError:
                        cancellation_token.cancel("Agent time budget exhausted")
                        for event in self._cancelled_tool_events(
                            reply.tool_calls[call_index:], "Agent time budget exhausted"
                        ):
                            yield event
                        for event in self._budget_events(
                            "time", used_tokens, time.monotonic() - started,
                            no_progress_streak,
                        ):
                            yield event
                        return
                    except ToolCancelledError as exc:
                        for cancelled_event in self._cancelled_tool_events(
                            reply.tool_calls[call_index:],
                            cancellation_token.reason,
                            active_error=exc,
                        ):
                            yield cancelled_event
                        yield AgentEvent(
                            type="run_finished",
                            usage=reply.usage,
                            details={
                                "stop_reason": "aborted",
                                "reason": cancellation_token.reason,
                            },
                        )
                        return
                    for batch_call, result in zip(batch, results, strict=True):
                        tool_message = ChatMessage(
                            role="tool",
                            content=result.content,
                            tool_call_id=batch_call.call_id,
                            name=batch_call.name,
                        )
                        self.messages.append(tool_message)
                        yield AgentEvent(
                            type="tool_finished",
                            message=tool_message,
                            tool_call=batch_call,
                            tool_result=result.content,
                            is_error=result.is_error,
                            details=result.details,
                        )
                        yield AgentEvent(type="message_added", message=tool_message)
                    call_index = end

                yield AgentEvent(type="turn_finished", message=assistant_message, usage=reply.usage)
                fingerprint = self._fingerprint(
                    reply,
                    [
                        self.messages[-len(batch):][index].content
                        for index in range(len(batch))
                    ],
                )
                no_progress_streak = (
                    no_progress_streak + 1
                    if fingerprint == last_progress_fingerprint
                    else 0
                )
                last_progress_fingerprint = fingerprint
                if no_progress_streak >= self.no_progress_limit:
                    for event in self._budget_events(
                        "no_progress", used_tokens, time.monotonic() - started,
                        no_progress_streak,
                    ):
                        yield event
                    return
                if used_tokens >= self.token_budget:
                    for event in self._budget_events(
                        "token", used_tokens, time.monotonic() - started,
                        no_progress_streak,
                    ):
                        yield event
                    return
                turn_number += 1
        except Exception as exc:
            yield AgentEvent(type="error", text=str(exc), is_error=True)
            yield AgentEvent(
                type="run_finished",
                is_error=True,
                details={"error": str(exc), "stop_reason": "error"},
            )
        finally:
            self._running = False
            clear_request_tool_names = getattr(
                self.tool_executor, "set_request_tool_names", None
            )
            if callable(clear_request_tool_names):
                clear_request_tool_names(None)
            if self._cancellation_token is cancellation_token:
                self._cancellation_token = None

    @staticmethod
    def _fingerprint(reply: AssistantReply, extra: object) -> str:
        payload = {
            "content": reply.content,
            "tool_calls": [
                {"name": call.name, "arguments": call.arguments}
                for call in reply.tool_calls
            ],
            "extra": extra,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _budget_events(
        self, budget_type: str, used_tokens: int, elapsed: float, no_progress_streak: int
    ) -> list[AgentEvent]:
        message = ChatMessage(
            role="assistant",
            content=(
                "执行预算已达到安全边界，任务暂停，尚未确认完成。"
                + (self.pause_message_provider() if self.pause_message_provider else
                   "已保留当前对话和工具结果；恢复时从最后结果继续核对。")
            ),
        )
        self.messages.append(message)
        details = {
            "stop_reason": "budget_exhausted",
            "budget_type": budget_type,
            "status": "paused",
            "resumable": True,
            "token_usage": used_tokens,
            "token_budget": self.token_budget,
            "elapsed_seconds": round(elapsed, 3),
            "time_budget_seconds": self.time_budget_seconds,
            "no_progress_streak": no_progress_streak,
            "no_progress_limit": self.no_progress_limit,
        }
        return [
            AgentEvent(type="message_added", message=message),
            AgentEvent(type="text_delta", text=message.content),
            AgentEvent(type="run_finished", message=message, details=details),
        ]

    def _request_messages(self, *, system_prompt: str | None = None) -> list[ChatMessage]:
        # The coding context transform owns the single request projection.
        # Applying ContextJournal.project here as well only walked the same
        # history twice and made the request path harder to explain.
        messages = (
            self.transform_context(self.messages)
            if self.transform_context
            else self.context_journal.project(self.messages)
        )
        if system_prompt is None:
            system_prompt = self.system_prompt_provider() if self.system_prompt_provider else self.system_prompt
        context_messages = (
            self.context_messages_provider() if self.context_messages_provider else []
        )
        # Retrieved memory is mutable reference data. Keep it immediately after
        # the stable policy prefix and before the live turn input so updates do
        # not invalidate the cached system prefix and cannot masquerade as
        # system instructions. Other context providers retain their historical
        # trailing placement for protocol compatibility.
        leading_context = [
            message
            for message in context_messages
            if message.name in {"memory_context", "semantic_memory"}
        ]
        trailing_context = [
            message
            for message in context_messages
            if message.name not in {"memory_context", "semantic_memory"}
        ]
        if system_prompt:
            return [
                ChatMessage(role="system", content=system_prompt),
                *leading_context,
                *messages,
                *trailing_context,
            ]
        return [*leading_context, *messages, *trailing_context]

    def _cancelled_tool_events(
        self,
        calls: list[ToolInvocation],
        reason: str,
        *,
        active_error: ToolCancelledError | None = None,
    ) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        for index, call in enumerate(calls):
            details = {
                "status": "cancelled",
                "cancelled": True,
                "reason": reason,
                "not_started": active_error is None or index > 0,
            }
            if index == 0 and active_error is not None:
                details.update(active_error.details)
                if active_error.stage:
                    details["stage"] = active_error.stage
            content = f"[CANCELLED] Tool call was not completed: {reason}"
            tool_message = ChatMessage(
                role="tool",
                content=content,
                tool_call_id=call.call_id,
                name=call.name,
            )
            self.messages.append(tool_message)
            events.append(
                AgentEvent(
                    type="tool_finished",
                    message=tool_message,
                    tool_call=call,
                    tool_result=content,
                    details=details,
                )
            )
            events.append(AgentEvent(type="message_added", message=tool_message))
        return events
