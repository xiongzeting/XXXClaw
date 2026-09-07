from __future__ import annotations

"""Environment-based LLM provider selection for MiniClaw."""

import os
from collections.abc import Mapping
from dataclasses import dataclass


DEFAULT_PRIMARY_BASE_URL = "https://ai.zxcoding.top/v1"
DEFAULT_PRIMARY_MODEL = "gpt-5.6-luna"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"


@dataclass(slots=True, frozen=True)
class LLMFallbackSettings:
    provider: str
    api_key: str
    base_url: str
    model_id: str
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    cached_input_cost_per_million: float = 0.0


@dataclass(slots=True, frozen=True)
class LLMSettings:
    provider: str
    api_key: str
    base_url: str
    model_id: str
    context_window: int
    max_output_tokens: int
    timeout_seconds: float = 120.0
    connect_timeout_seconds: float = 10.0
    first_token_timeout_seconds: float = 60.0
    idle_timeout_seconds: float = 30.0
    max_retries: int = 2
    retry_base_seconds: float = 0.5
    retry_max_seconds: float = 8.0
    retry_jitter_ratio: float = 0.2
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    cached_input_cost_per_million: float = 0.0
    fallbacks: tuple[LLMFallbackSettings, ...] = ()


def _first_value(environment: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = environment.get(name)
        if value:
            return value
    return None


def _positive_integer(environment: Mapping[str, str], name: str, fallback: int) -> int:
    raw = environment.get(name)
    if raw is None or not raw.strip():
        return fallback
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _non_negative_integer(environment: Mapping[str, str], name: str, fallback: int) -> int:
    raw = environment.get(name)
    if raw is None or not raw.strip():
        return fallback
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _non_negative_number(environment: Mapping[str, str], name: str, fallback: float = 0.0) -> float:
    raw = environment.get(name)
    if raw is None or not raw.strip():
        return fallback
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative number") from exc
    if value < 0:
        raise ValueError(f"{name} must be a non-negative number")
    return value


def _positive_number(environment: Mapping[str, str], name: str, fallback: float) -> float:
    value = _non_negative_number(environment, name, fallback)
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def load_llm_settings(
    *,
    provider: str | None = None,
    model_id: str | None = None,
    base_url: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> LLMSettings:
    env = os.environ if environment is None else environment
    selected = (provider or env.get("MINICLAW_PROVIDER") or "primary").strip().lower()
    aliases = {
        "primary": "primary",
        "openai": "primary",
        "openai-compatible": "primary",
        "zxcoding": "primary",
        "deepseek": "deepseek",
    }
    normalized = aliases.get(selected)
    if normalized is None:
        supported = ", ".join(sorted(aliases))
        raise ValueError(f"unknown LLM provider {selected!r}; choose one of: {supported}")

    if normalized == "primary":
        api_key = _first_value(
            env,
            "MINICLAW_PRIMARY_API_KEY",
            "MINICLAW_API_KEY",
        )
        resolved_base_url = base_url or _first_value(
            env,
            "MINICLAW_PRIMARY_BASE_URL",
            "MINICLAW_BASE_URL",
        ) or DEFAULT_PRIMARY_BASE_URL
        resolved_model = model_id or _first_value(
            env,
            "MINICLAW_PRIMARY_MODEL",
            "MINICLAW_MODEL",
        ) or DEFAULT_PRIMARY_MODEL
        missing_key_name = "MINICLAW_PRIMARY_API_KEY"
    else:
        api_key = _first_value(env, "MINICLAW_DEEPSEEK_API_KEY")
        resolved_base_url = (
            base_url
            or _first_value(env, "MINICLAW_DEEPSEEK_BASE_URL")
            or DEFAULT_DEEPSEEK_BASE_URL
        )
        resolved_model = (
            model_id
            or _first_value(env, "MINICLAW_DEEPSEEK_MODEL")
            or DEFAULT_DEEPSEEK_MODEL
        )
        missing_key_name = "MINICLAW_DEEPSEEK_API_KEY"

    if not api_key:
        raise ValueError(f"{missing_key_name} is required for provider {normalized}")
    timeout = _positive_number(env, "MINICLAW_LLM_TOTAL_TIMEOUT", 0.0) if env.get(
        "MINICLAW_LLM_TOTAL_TIMEOUT"
    ) else _positive_number(env, "MINICLAW_LLM_TIMEOUT", 120.0)
    connect_timeout = _positive_number(
        env, "MINICLAW_LLM_CONNECT_TIMEOUT", min(10.0, timeout)
    )
    first_token_timeout = _positive_number(
        env, "MINICLAW_LLM_FIRST_TOKEN_TIMEOUT", min(60.0, timeout)
    )
    idle_timeout = _positive_number(
        env, "MINICLAW_LLM_IDLE_TIMEOUT", min(30.0, timeout)
    )
    max_retries = _non_negative_integer(env, "MINICLAW_LLM_MAX_RETRIES", 2)
    retry_base = _positive_number(env, "MINICLAW_LLM_RETRY_BASE_SECONDS", 0.5)
    retry_max = _positive_number(env, "MINICLAW_LLM_RETRY_MAX_SECONDS", 8.0)
    retry_jitter = _non_negative_number(env, "MINICLAW_LLM_RETRY_JITTER_RATIO", 0.2)
    if retry_jitter > 1:
        raise ValueError("MINICLAW_LLM_RETRY_JITTER_RATIO must be between 0 and 1")
    if retry_base > retry_max:
        raise ValueError("MINICLAW_LLM_RETRY_BASE_SECONDS cannot exceed retry maximum")
    for name, value in (
        ("MINICLAW_LLM_CONNECT_TIMEOUT", connect_timeout),
        ("MINICLAW_LLM_FIRST_TOKEN_TIMEOUT", first_token_timeout),
        ("MINICLAW_LLM_IDLE_TIMEOUT", idle_timeout),
    ):
        if value > timeout:
            raise ValueError(f"{name} cannot exceed the total timeout")

    context_window = _positive_integer(env, "MINICLAW_CONTEXT_WINDOW", 128_000)
    max_output_tokens = _positive_integer(env, "MINICLAW_MAX_OUTPUT_TOKENS", 8_192)
    if max_output_tokens >= context_window:
        raise ValueError("MINICLAW_MAX_OUTPUT_TOKENS must be below MINICLAW_CONTEXT_WINDOW")

    fallbacks = _load_fallbacks(env, normalized, resolved_model, api_key, resolved_base_url)

    return LLMSettings(
        provider=normalized,
        api_key=api_key,
        base_url=resolved_base_url.rstrip("/"),
        model_id=resolved_model,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout,
        connect_timeout_seconds=connect_timeout,
        first_token_timeout_seconds=first_token_timeout,
        idle_timeout_seconds=idle_timeout,
        max_retries=max_retries,
        retry_base_seconds=retry_base,
        retry_max_seconds=retry_max,
        retry_jitter_ratio=retry_jitter,
        input_cost_per_million=_non_negative_number(
            env, "MINICLAW_PRICE_INPUT_PER_MILLION"
        ),
        output_cost_per_million=_non_negative_number(
            env, "MINICLAW_PRICE_OUTPUT_PER_MILLION"
        ),
        cached_input_cost_per_million=_non_negative_number(
            env, "MINICLAW_PRICE_CACHED_INPUT_PER_MILLION"
        ),
        fallbacks=fallbacks,
    )


def _load_fallbacks(
    env: Mapping[str, str],
    selected_provider: str,
    selected_model: str,
    selected_api_key: str,
    selected_base_url: str,
) -> tuple[LLMFallbackSettings, ...]:
    raw = env.get("MINICLAW_LLM_FALLBACKS", "").strip()
    if not raw:
        return ()
    aliases = {
        "primary": "primary",
        "openai": "primary",
        "openai-compatible": "primary",
        "zxcoding": "primary",
        "deepseek": "deepseek",
    }
    output: list[LLMFallbackSettings] = []
    seen = {(selected_provider, selected_model)}
    for position, item in enumerate(raw.split(","), start=1):
        spec = item.strip()
        if not spec:
            continue
        provider_name, separator, model_name = spec.partition(":")
        provider = aliases.get(provider_name.strip().lower())
        if provider is None:
            raise ValueError(f"Unknown fallback provider in item {position}: {provider_name}")
        if not separator or not model_name.strip():
            model_name = DEFAULT_PRIMARY_MODEL if provider == "primary" else DEFAULT_DEEPSEEK_MODEL
        model_name = model_name.strip()
        identity = (provider, model_name)
        if identity in seen:
            continue
        seen.add(identity)
        if provider == selected_provider:
            api_key = selected_api_key
            base_url = selected_base_url
        elif provider == "primary":
            api_key = _first_value(env, "MINICLAW_PRIMARY_API_KEY", "MINICLAW_API_KEY")
            base_url = _first_value(
                env,
                "MINICLAW_PRIMARY_BASE_URL",
                "MINICLAW_BASE_URL",
            ) or DEFAULT_PRIMARY_BASE_URL
        else:
            api_key = _first_value(env, "MINICLAW_DEEPSEEK_API_KEY")
            base_url = _first_value(env, "MINICLAW_DEEPSEEK_BASE_URL") or DEFAULT_DEEPSEEK_BASE_URL
        if not api_key:
            raise ValueError(
                f"Fallback {provider}:{model_name} requires its provider-specific API key"
            )
        price_prefix = "MINICLAW_PRIMARY" if provider == "primary" else "MINICLAW_DEEPSEEK"
        output.append(
            LLMFallbackSettings(
                provider=provider,
                api_key=api_key,
                base_url=base_url.rstrip("/"),
                model_id=model_name,
                input_cost_per_million=_non_negative_number(
                    env, f"{price_prefix}_PRICE_INPUT_PER_MILLION"
                ),
                output_cost_per_million=_non_negative_number(
                    env, f"{price_prefix}_PRICE_OUTPUT_PER_MILLION"
                ),
                cached_input_cost_per_million=_non_negative_number(
                    env, f"{price_prefix}_PRICE_CACHED_INPUT_PER_MILLION"
                ),
            )
        )
    return tuple(output)
