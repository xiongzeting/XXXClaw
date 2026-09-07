from __future__ import annotations

import os
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


SessionScope = Literal["thread", "channel", "user"]


@dataclass(slots=True, frozen=True)
class FeishuSettings:
    app_id: str
    app_secret: str
    session_scope: SessionScope = "thread"
    domain: str = "https://open.feishu.cn"
    queue_size: int = 5
    message_chunk_chars: int = 29_000
    max_concurrent_sessions: int = 8
    delivery_max_attempts: int = 3
    delivery_retry_base_seconds: float = 0.25
    inbound_stale_seconds: float = 300.0


def _first(environment: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = environment.get(name)
        if value and value.strip():
            return value.strip()
    return None


def load_feishu_settings(
    environment: Mapping[str, str] | None = None,
) -> FeishuSettings:
    env = os.environ if environment is None else environment
    app_id = _first(env, "MINICLAW_FEISHU_APP_ID")
    app_secret = _first(env, "MINICLAW_FEISHU_APP_SECRET")
    if not app_id:
        raise ValueError("MINICLAW_FEISHU_APP_ID is required")
    if not app_secret:
        raise ValueError("MINICLAW_FEISHU_APP_SECRET is required")
    scope = (_first(env, "MINICLAW_FEISHU_SESSION_SCOPE") or "thread").lower()
    if scope not in {"thread", "channel", "user"}:
        raise ValueError("MINICLAW_FEISHU_SESSION_SCOPE must be thread, channel, or user")
    domain = _first(env, "MINICLAW_FEISHU_DOMAIN") or "https://open.feishu.cn"
    queue_size = _positive_int(env, "MINICLAW_FEISHU_QUEUE_SIZE", 5)
    message_chunk_chars = _positive_int(env, "MINICLAW_FEISHU_MESSAGE_CHUNK_CHARS", 29_000)
    max_concurrent_sessions = _positive_int(
        env, "MINICLAW_FEISHU_MAX_CONCURRENT_SESSIONS", 8
    )
    delivery_max_attempts = _positive_int(
        env, "MINICLAW_FEISHU_DELIVERY_MAX_ATTEMPTS", 3
    )
    delivery_retry_base_seconds = _non_negative_float(
        env, "MINICLAW_FEISHU_DELIVERY_RETRY_BASE_SECONDS", 0.25
    )
    inbound_stale_seconds = _positive_float(
        env, "MINICLAW_FEISHU_INBOUND_STALE_SECONDS", 300.0
    )
    return FeishuSettings(
        app_id=app_id,
        app_secret=app_secret,
        session_scope=scope,  # type: ignore[arg-type]
        domain=domain.rstrip("/"),
        queue_size=queue_size,
        message_chunk_chars=message_chunk_chars,
        max_concurrent_sessions=max_concurrent_sessions,
        delivery_max_attempts=delivery_max_attempts,
        delivery_retry_base_seconds=delivery_retry_base_seconds,
        inbound_stale_seconds=inbound_stale_seconds,
    )


def _positive_int(environment: Mapping[str, str], name: str, fallback: int) -> int:
    raw = _first(environment, name)
    if raw is None:
        return fallback
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_float(environment: Mapping[str, str], name: str, fallback: float) -> float:
    value = _non_negative_float(environment, name, fallback)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _non_negative_float(environment: Mapping[str, str], name: str, fallback: float) -> float:
    raw = _first(environment, name)
    if raw is None:
        return fallback
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative number") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a non-negative number")
    return value
