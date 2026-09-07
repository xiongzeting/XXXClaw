from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .config import SessionScope


FEISHU_PREFIX = "feishu-"


@dataclass(slots=True, frozen=True)
class FeishuConversation:
    conversation_id: str
    session_key: str
    reply_message_id: str


@dataclass(slots=True, frozen=True)
class FeishuInboundMessage:
    message_id: str
    chat_id: str
    user_id: str
    tenant_id: str
    chat_type: str
    message_type: str
    text: str
    root_id: str = ""
    parent_id: str = ""
    thread_id: str = ""
    mentioned: bool = False

    @property
    def namespaced_chat_id(self) -> str:
        return f"{FEISHU_PREFIX}{self.chat_id}"

    @property
    def namespaced_user_id(self) -> str:
        return f"{FEISHU_PREFIX}{self.user_id}"


def sanitize_session_key(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).lstrip(".")
    return (sanitized or "conversation")[:180]


def build_conversation(
    message: FeishuInboundMessage,
    scope: SessionScope,
) -> FeishuConversation:
    channel = message.namespaced_chat_id
    user = message.namespaced_user_id
    is_dm = message.chat_type == "p2p"
    thread_root = message.root_id or message.thread_id or message.parent_id or message.message_id
    if scope == "channel":
        conversation_id = f"{channel}--channel"
    elif scope == "user":
        conversation_id = f"{channel}--user--{user}"
    elif is_dm:
        conversation_id = f"dm--{channel}"
    else:
        conversation_id = f"{channel}--thread--{thread_root}"
    return FeishuConversation(
        conversation_id=conversation_id,
        session_key=sanitize_session_key(conversation_id),
        reply_message_id=thread_root if not is_dm else message.message_id,
    )


def extract_message_text(message_type: str, raw_content: str, mention_keys: list[str]) -> str:
    try:
        payload = json.loads(raw_content or "{}")
    except json.JSONDecodeError:
        return raw_content.strip()
    if message_type == "text":
        text = str(payload.get("text") or "")
    elif message_type == "post":
        text = _extract_post_text(payload)
    else:
        text = ""
    for key in mention_keys:
        if key:
            text = text.replace(key, " ")
    return " ".join(text.split()).strip()


def _extract_post_text(payload: Any) -> str:
    values: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            tag = value.get("tag")
            if tag in {"text", "a", "code_block"} and value.get("text"):
                values.append(str(value["text"]))
            elif tag == "at" and value.get("user_name"):
                values.append(f"@{value['user_name']}")
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return "\n".join(part.strip() for part in values if part.strip())
