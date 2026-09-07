from __future__ import annotations

from .config import LLMSettings
from .openai_compatible import OpenAICompatibleClient, OpenAICompatibleRoute
from .types import ModelProfile


def create_model_client(settings: LLMSettings) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout_seconds=settings.timeout_seconds,
        provider=settings.provider,
        model_id=settings.model_id,
        connect_timeout_seconds=settings.connect_timeout_seconds,
        first_token_timeout_seconds=settings.first_token_timeout_seconds,
        idle_timeout_seconds=settings.idle_timeout_seconds,
        max_retries=settings.max_retries,
        retry_base_seconds=settings.retry_base_seconds,
        retry_max_seconds=settings.retry_max_seconds,
        retry_jitter_ratio=settings.retry_jitter_ratio,
        fallback_routes=tuple(
            OpenAICompatibleRoute(
                provider=route.provider,
                api_key=route.api_key,
                base_url=route.base_url,
                model_id=route.model_id,
                input_cost_per_million=route.input_cost_per_million,
                output_cost_per_million=route.output_cost_per_million,
                cached_input_cost_per_million=route.cached_input_cost_per_million,
            )
            for route in settings.fallbacks
        ),
    )


def model_profile_from_settings(settings: LLMSettings) -> ModelProfile:
    return ModelProfile(
        model_id=settings.model_id,
        context_window=settings.context_window,
        max_output_tokens=settings.max_output_tokens,
        input_cost_per_million=settings.input_cost_per_million,
        output_cost_per_million=settings.output_cost_per_million,
        cached_input_cost_per_million=settings.cached_input_cost_per_million,
    )
