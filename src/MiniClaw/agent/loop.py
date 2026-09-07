from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace

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
        max_turns: int = 32,
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
        self.max_turns = max_turns
        self.transform_context = transform_context
        self.system_prompt_provider = system_prompt_provider
        self.context_messages_provider = context_messages_provider
        self.final_guard = final_guard
        self.pause_message_provider = pause_message_provider
        self.context_diagnostics_provider = context_diagnostics_provider
        self.final_response_guard = final_response_guard
        self.request_tools_provider = request_tools_provider
        self.context_updates_provider = context_updates_provider
        self.context_journal = ContextJournal()
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
        try:
            yield AgentEvent(type="run_started")
            if prompt is not None:
                self.pending_request = None
                user_message = ChatMessage(role="user", content=prompt)
                self.messages.append(user_message)
                yield AgentEvent(type="message_added", message=user_message)

            for turn_number in range(start_turn, self.max_turns + 1):
                if cancellation_token.cancelled:
                    yield AgentEvent(
                        type="run_finished",
                        details={
                            "stop_reason": "aborted",
                            "reason": cancellation_token.reason,
                        },
                    )
                    return
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
                if not recovering and turn_number >= max(1, self.max_turns - 2):
                    request_messages = [*request_messages, ChatMessage(role="system", content=(
                        f"Execution budget: {self.max_turns - turn_number + 1} model calls remain; "
                        "the hard limit will not increase. Finish the remaining work or explain the unfinished parts "
                        "and the next action in your response. The session is saved automatically. "
                        "Do not restart completed work or claim completion with unchecked requirements."
                    ))]
                request = ModelRequest(
                    profile=self.profile,
                    messages=request_messages,
                    tools=(self.request_tools_provider() if self.request_tools_provider else self.tool_executor.definitions()),
                    metadata={"purpose": "agent", "buffer_network_retries": True,
                              **({"context_projection": self.context_diagnostics_provider()}
                                 if self.context_diagnostics_provider else {})},
                    cancellation_token=cancellation_token,
                )
                if recovering:
                    request = self.pending_request
                    request.cancellation_token = cancellation_token
                request.metadata['request_prefix'] = self.prefix_diagnostics.observe(request)
                request.metadata['context_updates'] = dict(self.context_journal.last_details)
                self.pending_request = request
                async for model_event in self.model_client.stream(request):
                    if model_event.type == "text_delta":
                        if self.final_guard is None and self.final_response_guard is None:
                            yield AgentEvent(type="text_delta", text=model_event.text)
                    elif model_event.type == "error":
                        yield AgentEvent(type="error", text=model_event.error or "model error", is_error=True)
                    elif model_event.type == "completed":
                        reply = model_event.reply

                if reply is None:
                    raise RuntimeError("model stream ended without a completed reply")
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

                for call_index, call in enumerate(reply.tool_calls):
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
                    yield AgentEvent(type="tool_started", tool_call=call)
                    try:
                        if self.request_tools_provider is not None and call.name not in {d['name'] for d in request.tools}:
                            result = SimpleNamespace(content='Tool unavailable in the current execution state. Deliver the verified result.',
                                                     is_error=True, details={'status':'unavailable', 'not_started':True})
                        else:
                            result = await self.tool_executor.execute(call, cancellation_token)
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
                    tool_message = ChatMessage(
                        role="tool",
                        content=result.content,
                        tool_call_id=call.call_id,
                        name=call.name,
                    )
                    self.messages.append(tool_message)
                    yield AgentEvent(
                        type="tool_finished",
                        message=tool_message,
                        tool_call=call,
                        tool_result=result.content,
                        is_error=result.is_error,
                        details=result.details,
                    )
                    yield AgentEvent(type="message_added", message=tool_message)

                yield AgentEvent(type="turn_finished", message=assistant_message, usage=reply.usage)

            message = ChatMessage(role="assistant", content=(
                f"已达到本轮 {self.max_turns} 步执行上限，任务暂停，尚未确认完成。"
                + (self.pause_message_provider() if self.pause_message_provider else
                   "已保留当前对话和工具结果；恢复时应从最后结果继续，核对剩余工作与验收项。")
            ))
            self.messages.append(message)
            yield AgentEvent(type="message_added", message=message)
            yield AgentEvent(type="text_delta", text=message.content)
            yield AgentEvent(type="run_finished", message=message, details={
                "stop_reason": "budget_exhausted", "status": "paused", "resumable": True,
                "turn_limit": self.max_turns,
            })
        except Exception as exc:
            yield AgentEvent(type="error", text=str(exc), is_error=True)
            yield AgentEvent(
                type="run_finished",
                is_error=True,
                details={"error": str(exc), "stop_reason": "error"},
            )
        finally:
            self._running = False
            if self._cancellation_token is cancellation_token:
                self._cancellation_token = None

    def _request_messages(self, *, system_prompt: str | None = None) -> list[ChatMessage]:
        messages = self.context_journal.project(self.messages)
        if self.transform_context:
            messages = self.transform_context(messages)
        if system_prompt is None:
            system_prompt = self.system_prompt_provider() if self.system_prompt_provider else self.system_prompt
        context_messages = (
            self.context_messages_provider() if self.context_messages_provider else []
        )
        if system_prompt:
            return [
                ChatMessage(role="system", content=system_prompt),
                *messages,
                *context_messages,
            ]
        return [*messages, *context_messages]

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
