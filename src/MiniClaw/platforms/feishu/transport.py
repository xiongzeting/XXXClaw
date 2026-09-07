from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from .config import FeishuSettings


class FeishuApiError(RuntimeError):
    pass


@dataclass(slots=True)
class FeishuTransport:
    settings: FeishuSettings
    _client: Any = field(init=False, repr=False)
    _message_types: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # lark-oapi imports its websocket client eagerly and creates a module-level
        # asyncio loop. Keep that side effect out of the generic router/runtime
        # import path; only a real Feishu transport should load the SDK.
        import lark_oapi as lark
        from lark_oapi.api.im import v1 as message_types
        from lark_oapi.ws import client as lark_ws_client

        self._message_types = message_types
        self._client = (
            lark.Client.builder()
            .app_id(self.settings.app_id)
            .app_secret(self.settings.app_secret)
            .domain(self.settings.domain)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
        # The REST transport does not use the SDK websocket loop. Close the
        # eagerly-created, otherwise idle loop so webhook/API-only processes
        # also exit cleanly. A constructed/running websocket client owns tasks
        # on the loop and is therefore left alone.
        websocket_loop = lark_ws_client.loop
        if (
            not websocket_loop.is_closed()
            and not websocket_loop.is_running()
            and not asyncio.all_tasks(websocket_loop)
        ):
            websocket_loop.close()

    async def reply(self, message_id: str, text: str, *, in_thread: bool) -> str:
        return await asyncio.to_thread(self._reply_sync, message_id, text, in_thread)

    async def update(self, message_id: str, text: str) -> None:
        await asyncio.to_thread(self._update_sync, message_id, text)

    def _reply_sync(self, message_id: str, text: str, in_thread: bool) -> str:
        message_types = self._message_types
        body = (
            message_types.ReplyMessageRequestBody.builder()
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .msg_type("text")
            .reply_in_thread(in_thread)
            .uuid(uuid.uuid4().hex)
            .build()
        )
        request = (
            message_types.ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        response = self._client.im.v1.message.reply(request)
        self._ensure_success(response, "reply message")
        return str(response.data.message_id)

    def _update_sync(self, message_id: str, text: str) -> None:
        message_types = self._message_types
        body = (
            message_types.PatchMessageRequestBody.builder()
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        request = (
            message_types.PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        response = self._client.im.v1.message.patch(request)
        self._ensure_success(response, "update message")

    @staticmethod
    def _ensure_success(response: Any, operation: str) -> None:
        if response.success():
            return
        request_id = ""
        if getattr(response, "get_log_id", None):
            request_id = str(response.get_log_id() or "")
        raise FeishuApiError(
            f"Feishu {operation} failed: code={response.code}, msg={response.msg}, log_id={request_id}"
        )
