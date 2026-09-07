from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from MiniClaw.coding_agent.approval import (
    ApprovalInbox,
    ApprovalRequest,
    ApprovalSettings,
    load_approval_settings,
    parse_approval_response,
)
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.goal.config import GoalConfig, GoalJudgeConfig
from MiniClaw.coding_agent.goal.prompts import parse_goal_command
from MiniClaw.llm.client import ModelClient
from MiniClaw.llm.types import ModelProfile
from MiniClaw.coding_agent.runtime import RuntimeSettings
from MiniClaw.platforms.delivery import DeliveryManager

from .config import FeishuSettings
from .dedupe import PersistentEventDeduplicator
from .models import FeishuInboundMessage, build_conversation
from .transport import FeishuTransport


@dataclass(slots=True)
class _ConversationQueue:
    queue: asyncio.Queue[FeishuInboundMessage]
    worker: asyncio.Task[None] | None = None


@dataclass(slots=True)
class FeishuAssistantRouter:
    workspace: Path
    model_client: ModelClient
    profile: ModelProfile
    settings: FeishuSettings
    transport: FeishuTransport
    goal_config: GoalConfig | None = None
    goal_judge_config: GoalJudgeConfig | None = None
    runtime_settings: RuntimeSettings | None = None
    approval_settings: ApprovalSettings | None = None
    trace_provider: str = "openai-compatible"
    _assistants: dict[str, CodingAssistant] = field(default_factory=dict, init=False)
    _queues: dict[str, _ConversationQueue] = field(default_factory=dict, init=False)
    _dedupe: PersistentEventDeduplicator = field(init=False)
    _approval_inbox: ApprovalInbox = field(default_factory=ApprovalInbox, init=False)
    _active_messages: dict[str, FeishuInboundMessage] = field(default_factory=dict, init=False)
    _delivery: DeliveryManager = field(init=False)
    _session_slots: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self.workspace = self.workspace.resolve()
        self._dedupe = PersistentEventDeduplicator(
            self.workspace / ".aster" / "feishu" / "events.jsonl",
            stale_after_seconds=self.settings.inbound_stale_seconds,
        )
        self._delivery = DeliveryManager(
            self.workspace / ".aster" / "feishu" / "delivery.jsonl",
            max_attempts=self.settings.delivery_max_attempts,
            retry_base_seconds=self.settings.delivery_retry_base_seconds,
        )
        self._session_slots = asyncio.Semaphore(self.settings.max_concurrent_sessions)
        if self.approval_settings is None:
            self.approval_settings = load_approval_settings(self.workspace)

    def has_session(self, message: FeishuInboundMessage) -> bool:
        conversation = build_conversation(message, self.settings.session_scope)
        if conversation.session_key in self._assistants:
            return True
        return (
            self.workspace
            / ".aster"
            / "feishu"
            / "sessions"
            / conversation.session_key
            / "context.jsonl"
        ).exists()

    async def close(self) -> None:
        """Cancel active work and release per-conversation worker references."""

        for assistant in self._assistants.values():
            assistant.cancel("Feishu router is shutting down")
        workers = [
            state.worker
            for state in self._queues.values()
            if state.worker is not None and not state.worker.done()
        ]
        if workers:
            done, pending = await asyncio.wait(workers, timeout=5)
            for worker in pending:
                worker.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for worker in done:
                if not worker.cancelled():
                    worker.exception()
        for state in self._queues.values():
            while True:
                try:
                    message = state.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self._dedupe.fail(message.message_id, "Feishu router shut down before processing")
                state.queue.task_done()
        self._active_messages.clear()
        self._queues.clear()

    async def submit(self, message: FeishuInboundMessage) -> None:
        if not self._dedupe.claim(message.message_id):
            return
        conversation = build_conversation(message, self.settings.session_scope)
        goal_command = parse_goal_command(message.text)
        if (
            message.text.strip().lower() in {"/cancel", "cancel", "取消任务"}
            or goal_command is not None
            and goal_command.action == "cancel"
        ):
            assistant = self._assistant(conversation.session_key, message)
            reason = (
                goal_command.reason
                if goal_command is not None and goal_command.reason
                else "Task cancelled from Feishu"
            )
            cancelled = assistant.cancel(reason)
            await self._reply(
                conversation.session_key,
                message.message_id,
                "已发送取消信号，正在回收模型、工具和运行进程。"
                if cancelled
                else "当前会话没有可取消的任务。",
                in_thread=message.chat_type == "group",
            )
            self._dedupe.complete(message.message_id)
            return
        approval_response = parse_approval_response(message.text)
        if approval_response is not None:
            status = self._approval_inbox.resolve(
                conversation.session_key,
                message.user_id,
                approval_response,
            )
            text = {
                "approve": f"已批准操作 `{approval_response.approval_id}`，任务继续执行。",
                "deny": f"已拒绝操作 `{approval_response.approval_id}`。",
                "wrong-user": "只有发起当前任务的用户可以处理这条审批。",
                "already-decided": "这条审批已经处理。",
                "not-found": "未找到这条待处理审批，可能已经超时或属于其他会话。",
            }[status]
            await self._reply(
                conversation.session_key,
                message.message_id,
                text,
                in_thread=message.chat_type == "group",
            )
            self._dedupe.complete(message.message_id)
            return
        state = self._queues.get(conversation.session_key)
        if state is None:
            state = _ConversationQueue(asyncio.Queue(maxsize=self.settings.queue_size))
            self._queues[conversation.session_key] = state
        try:
            state.queue.put_nowait(message)
        except asyncio.QueueFull:
            await self._reply(
                conversation.session_key,
                message.message_id,
                "当前会话队列已满，请稍后再试。",
                in_thread=message.chat_type == "group",
            )
            self._dedupe.complete(message.message_id)
            return
        if state.worker is None or state.worker.done():
            state.worker = asyncio.create_task(self._consume(conversation.session_key, state))

    async def _consume(self, session_key: str, state: _ConversationQueue) -> None:
        while not state.queue.empty():
            message = await state.queue.get()
            try:
                async with self._session_slots:
                    await self._handle(session_key, message)
                self._dedupe.complete(message.message_id)
            except Exception as exc:
                try:
                    await self._reply(
                        session_key,
                        message.message_id,
                        f"MiniClaw 处理失败：{type(exc).__name__}: {exc}",
                        in_thread=message.chat_type == "group",
                    )
                    self._dedupe.complete(message.message_id)
                except Exception:
                    self._dedupe.fail(message.message_id, f"{type(exc).__name__}: {exc}")
            finally:
                state.queue.task_done()

    async def _handle(self, session_key: str, message: FeishuInboundMessage) -> None:
        progress_id = await self._reply(
            session_key,
            message.message_id,
            "MiniClaw 正在处理…",
            in_thread=message.chat_type == "group",
        )
        self._active_messages[session_key] = message
        try:
            assistant = self._assistant(session_key, message)
            final_text = ""
            error_text = ""
            artifacts: list[dict[str, object]] = []
            command = parse_goal_command(message.text)
            if command and command.action == "status":
                final_text = assistant.goal_status()
            elif command and command.action == "cancel":
                assistant.cancel(command.reason or "Goal cancelled by the user")
                state = assistant.goal_store.read()
                final_text = f"Goal 已取消。\n{state.final_result if state else ''}"
            else:
                if command and command.action == "start":
                    assistant.create_goal(
                        command.goal or "",
                        command.acceptance_criteria or [],
                        requested_by=message.user_id,
                    )
                    events = assistant.run_goal(message.text)
                elif command and command.action == "resume":
                    assistant.goal_store.resume()
                    events = assistant.run_goal()
                else:
                    events = assistant.run(message.text)
                async for event in events:
                    if event.type == "run_finished":
                        if event.details and isinstance(event.details.get("artifacts"), list):
                            artifacts = event.details["artifacts"]
                        if event.message and event.message.content.strip():
                            final_text = event.message.content.strip()
                        elif event.details and event.details.get("stop_reason") == "aborted":
                            final_text = f"任务已取消：{event.details.get('reason') or '用户取消'}"
                    elif event.type == "error":
                        error_text = event.text.strip()
                state = assistant.goal_store.read()
                if not final_text and state and state.status in {
                    "complete",
                    "failed",
                    "cancelled",
                    "waiting_for_user",
                }:
                    final_text = state.final_result or assistant.goal_status()
            result = final_text or (f"MiniClaw 处理失败：{error_text}" if error_text else "任务已结束，但模型没有返回文本。")
            artifact_paths = list(dict.fromkeys(
                str(item.get("path"))
                for item in artifacts
                if isinstance(item, dict) and item.get("path")
            ))
            chunks = self._chunks(result)
            await self._update(
                session_key,
                progress_id,
                chunks[0],
                key=f"{message.message_id}:final:0",
            )
            for chunk_index, chunk in enumerate(chunks[1:], start=1):
                await self._reply(
                    session_key,
                    message.message_id,
                    chunk,
                    in_thread=message.chat_type == "group",
                    key=f"{message.message_id}:final:{chunk_index}",
                )
            if artifact_paths:
                await self._reply(
                    session_key,
                    message.message_id,
                    "产物：\n" + "\n".join(f"- {path}" for path in artifact_paths),
                    in_thread=message.chat_type == "group",
                    key=f"{message.message_id}:artifacts",
                )
        finally:
            self._active_messages.pop(session_key, None)

    def _assistant(
        self,
        session_key: str,
        message: FeishuInboundMessage | None = None,
    ) -> CodingAssistant:
        assistant = self._assistants.get(session_key)
        if assistant is not None:
            return assistant
        session_path = (
            self.workspace
            / ".aster"
            / "feishu"
            / "sessions"
            / session_key
            / "context.jsonl"
        )
        assistant = CodingAssistant(
            model_client=self.model_client,
            profile=self.profile,
            workspace=self.workspace,
            session_path=session_path,
            session_id=session_key,
            goal_config=self.goal_config,
            goal_judge_config=self.goal_judge_config,
            runtime_settings=self.runtime_settings,
            approval_settings=self.approval_settings,
            approval_handler=lambda request: self._request_approval(session_key, request),
            trace_channel="feishu",
            trace_provider=self.trace_provider,
            memory_user_scope=message.namespaced_user_id if message else "",
            memory_channel_scope=message.namespaced_chat_id if message else "",
        )
        self._assistants[session_key] = assistant
        return assistant

    async def _request_approval(
        self,
        session_key: str,
        request: ApprovalRequest,
    ) -> bool:
        message = self._active_messages.get(session_key)
        if message is None:
            raise RuntimeError("No active Feishu message is available for approval")

        async def notify(text: str) -> None:
            await self._reply(
                session_key,
                message.message_id,
                text,
                in_thread=message.chat_type == "group",
            )

        return await self._approval_inbox.wait(
            session_key,
            message.user_id,
            request,
            notify,
        )

    async def _reply(
        self,
        session_key: str,
        message_id: str,
        text: str,
        *,
        in_thread: bool,
        key: str | None = None,
    ) -> str:
        content_key = hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]
        result = await self._delivery.deliver(
            f"{session_key}:reply:{key or message_id + ':' + content_key}",
            "feishu.reply",
            {
                "session_key": session_key,
                "message_id": message_id,
                "text": text,
                "in_thread": in_thread,
            },
            lambda: self.transport.reply(message_id, text, in_thread=in_thread),
        )
        return str(result or "")

    async def _update(
        self,
        session_key: str,
        message_id: str,
        text: str,
        *,
        key: str,
    ) -> None:
        await self._delivery.deliver(
            f"{session_key}:update:{key}",
            "feishu.update",
            {
                "session_key": session_key,
                "message_id": message_id,
                "text": text,
            },
            lambda: self.transport.update(message_id, text),
        )

    def _chunks(self, text: str) -> list[str]:
        limit = self.settings.message_chunk_chars
        return [text[index : index + limit] for index in range(0, len(text), limit)] or [""]
