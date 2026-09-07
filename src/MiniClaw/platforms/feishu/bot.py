from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import Any

from .config import FeishuSettings
from .models import FeishuInboundMessage, extract_message_text
from .router import FeishuAssistantRouter


class FeishuBot:
    """Official Feishu SDK long connection that forwards events into MiniClaw."""

    def __init__(self, settings: FeishuSettings, router: FeishuAssistantRouter) -> None:
        # The SDK creates a module-level event loop during import. Import it only
        # when the long-connection adapter is actually constructed.
        import lark_oapi as lark
        import lark_oapi.ws as lark_ws
        from lark_oapi.ws import client as lark_ws_client

        if lark_ws_client.loop.is_closed():
            lark_ws_client.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(lark_ws_client.loop)

        self.settings = settings
        self.router = router
        self._worker_loop: asyncio.AbstractEventLoop | None = None
        self._worker_thread: threading.Thread | None = None
        self._sdk_loop: asyncio.AbstractEventLoop = lark_ws_client.loop
        self._closed = False
        dispatcher = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message)
            .build()
        )
        self._ws = lark_ws.Client(
            settings.app_id,
            settings.app_secret,
            log_level=lark.LogLevel.WARNING,
            event_handler=dispatcher,
            domain=settings.domain,
            auto_reconnect=True,
            source="miniclaw",
        )

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("Feishu bot has already been closed")
        if self._worker_thread is not None:
            raise RuntimeError("Feishu bot has already been started")
        self._worker_loop = asyncio.new_event_loop()
        self._worker_thread = threading.Thread(
            target=self._run_worker_loop,
            name="miniclaw-feishu-worker",
            daemon=True,
        )
        self._worker_thread.start()
        try:
            self._ws.start()
        finally:
            self.close()

    def close(self) -> None:
        """Stop MiniClaw and SDK loops after disconnect, safe to call repeatedly."""

        if self._closed:
            return
        self._closed = True
        worker_loop = self._worker_loop
        worker_thread = self._worker_thread
        if worker_loop is not None and worker_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self.router.close(), worker_loop)
            with contextlib.suppress(Exception):
                future.result(timeout=5)
            worker_loop.call_soon_threadsafe(worker_loop.stop)
        if worker_thread is not None and worker_thread.is_alive():
            worker_thread.join(timeout=5)
        if worker_loop is not None and not worker_loop.is_running():
            with contextlib.suppress(Exception):
                worker_loop.close()
        self._close_sdk_loop()

    def _close_sdk_loop(self) -> None:
        loop = self._sdk_loop
        if loop.is_closed() or loop.is_running():
            return

        async def shutdown() -> None:
            disconnect = getattr(self._ws, "_disconnect", None)
            if disconnect is not None:
                with contextlib.suppress(Exception):
                    await disconnect()
            current = asyncio.current_task(loop=loop)
            pending = [task for task in asyncio.all_tasks(loop) if task is not current]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        with contextlib.suppress(Exception):
            loop.run_until_complete(shutdown())
        with contextlib.suppress(Exception):
            loop.close()

    def _run_worker_loop(self) -> None:
        loop = self._worker_loop
        if loop is None:
            return
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def _on_message(self, event: Any) -> None:
        inbound = self._normalize(event)
        if inbound is None:
            return
        is_managed_reply = (
            inbound.chat_type == "group"
            and self.settings.session_scope == "thread"
            and bool(inbound.root_id or inbound.thread_id or inbound.parent_id)
            and self.router.has_session(inbound)
        )
        if inbound.chat_type != "p2p" and not inbound.mentioned and not is_managed_reply:
            return
        loop = self._worker_loop
        if loop is None or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self.router.submit(inbound), loop)

    @staticmethod
    def _normalize(event: Any) -> FeishuInboundMessage | None:
        data = getattr(event, "event", None)
        sender = getattr(data, "sender", None)
        message = getattr(data, "message", None)
        if not sender or not message or getattr(sender, "sender_type", "") != "user":
            return None
        sender_id = getattr(sender, "sender_id", None)
        user_id = getattr(sender_id, "open_id", None) or getattr(sender_id, "user_id", None)
        message_id = getattr(message, "message_id", None)
        chat_id = getattr(message, "chat_id", None)
        if not user_id or not message_id or not chat_id:
            return None
        mentions = list(getattr(message, "mentions", None) or [])
        mention_keys = [str(getattr(mention, "key", "") or "") for mention in mentions]
        message_type = str(getattr(message, "message_type", "") or "")
        text = extract_message_text(
            message_type,
            str(getattr(message, "content", "") or ""),
            mention_keys,
        )
        if not text:
            if message_type:
                text = f"用户发送了暂不支持的飞书消息类型：{message_type}"
            else:
                return None
        return FeishuInboundMessage(
            message_id=str(message_id),
            chat_id=str(chat_id),
            user_id=str(user_id),
            tenant_id=str(getattr(sender, "tenant_key", "") or ""),
            chat_type=str(getattr(message, "chat_type", "") or ""),
            message_type=message_type,
            text=text,
            root_id=str(getattr(message, "root_id", "") or ""),
            parent_id=str(getattr(message, "parent_id", "") or ""),
            thread_id=str(getattr(message, "thread_id", "") or ""),
            mentioned=bool(mentions),
        )
