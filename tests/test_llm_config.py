from __future__ import annotations

import unittest

from MiniClaw.llm.config import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_PRIMARY_BASE_URL,
    DEFAULT_PRIMARY_MODEL,
    load_llm_settings,
)


class LLMConfigTests(unittest.TestCase):
    def test_primary_is_default(self) -> None:
        settings = load_llm_settings(environment={"MINICLAW_PRIMARY_API_KEY": "secret"})
        self.assertEqual(settings.provider, "primary")
        self.assertEqual(settings.base_url, DEFAULT_PRIMARY_BASE_URL)
        self.assertEqual(settings.model_id, DEFAULT_PRIMARY_MODEL)
        self.assertEqual(settings.context_window, 128_000)
        self.assertEqual(settings.max_output_tokens, 8_192)

    def test_deepseek_profile(self) -> None:
        settings = load_llm_settings(
            provider="deepseek",
            environment={"MINICLAW_DEEPSEEK_API_KEY": "secret"},
        )
        self.assertEqual(settings.provider, "deepseek")
        self.assertEqual(settings.base_url, DEFAULT_DEEPSEEK_BASE_URL)
        self.assertEqual(settings.model_id, DEFAULT_DEEPSEEK_MODEL)

    def test_cli_overrides_environment_model_and_url(self) -> None:
        settings = load_llm_settings(
            provider="zxcoding",
            model_id="custom-model",
            base_url="https://example.test/v1/",
            environment={"MINICLAW_PRIMARY_API_KEY": "secret"},
        )
        self.assertEqual(settings.model_id, "custom-model")
        self.assertEqual(settings.base_url, "https://example.test/v1")

    def test_provider_specific_key_is_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "MINICLAW_DEEPSEEK_API_KEY"):
            load_llm_settings(provider="deepseek", environment={})

    def test_boolean_like_timeout_is_not_accepted_as_number(self) -> None:
        with self.assertRaisesRegex(ValueError, "MINICLAW_LLM_TIMEOUT"):
            load_llm_settings(
                environment={
                    "MINICLAW_PRIMARY_API_KEY": "secret",
                    "MINICLAW_LLM_TIMEOUT": "never",
                }
            )

    def test_model_limits_can_be_overridden(self) -> None:
        settings = load_llm_settings(
            environment={
                "MINICLAW_PRIMARY_API_KEY": "secret",
                "MINICLAW_CONTEXT_WINDOW": "400000",
                "MINICLAW_MAX_OUTPUT_TOKENS": "16000",
            }
        )
        self.assertEqual(settings.context_window, 400_000)
        self.assertEqual(settings.max_output_tokens, 16_000)

    def test_output_limit_must_fit_context_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be below"):
            load_llm_settings(
                environment={
                    "MINICLAW_PRIMARY_API_KEY": "secret",
                    "MINICLAW_CONTEXT_WINDOW": "8000",
                    "MINICLAW_MAX_OUTPUT_TOKENS": "8000",
                }
            )

    def test_trace_pricing_can_be_configured_without_affecting_provider_calls(self) -> None:
        settings = load_llm_settings(
            environment={
                "MINICLAW_PRIMARY_API_KEY": "secret",
                "MINICLAW_PRICE_INPUT_PER_MILLION": "1.25",
                "MINICLAW_PRICE_OUTPUT_PER_MILLION": "8",
                "MINICLAW_PRICE_CACHED_INPUT_PER_MILLION": "0.2",
            }
        )
        self.assertEqual(settings.input_cost_per_million, 1.25)
        self.assertEqual(settings.output_cost_per_million, 8)
        self.assertEqual(settings.cached_input_cost_per_million, 0.2)

    def test_miniclaw_provider_environment_is_used(self) -> None:
        settings = load_llm_settings(
            environment={
                "MINICLAW_PROVIDER": "deepseek",
                "MINICLAW_DEEPSEEK_API_KEY": "secret",
                "MINICLAW_DEEPSEEK_BASE_URL": "https://api.deepseek.example",
                "MINICLAW_DEEPSEEK_MODEL": "deepseek-test",
                "MINICLAW_CONTEXT_WINDOW": "1000000",
                "MINICLAW_MAX_OUTPUT_TOKENS": "16384",
            }
        )
        self.assertEqual(settings.provider, "deepseek")
        self.assertEqual(settings.base_url, "https://api.deepseek.example")
        self.assertEqual(settings.model_id, "deepseek-test")
        self.assertEqual(settings.context_window, 1_000_000)
        self.assertEqual(settings.max_output_tokens, 16_384)

    def test_stream_timeouts_retry_and_fallback_routes_are_configurable(self) -> None:
        settings = load_llm_settings(
            environment={
                "MINICLAW_PRIMARY_API_KEY": "primary-key",
                "MINICLAW_DEEPSEEK_API_KEY": "deepseek-key",
                "MINICLAW_LLM_TOTAL_TIMEOUT": "90",
                "MINICLAW_LLM_CONNECT_TIMEOUT": "5",
                "MINICLAW_LLM_FIRST_TOKEN_TIMEOUT": "20",
                "MINICLAW_LLM_IDLE_TIMEOUT": "15",
                "MINICLAW_LLM_MAX_RETRIES": "0",
                "MINICLAW_LLM_RETRY_BASE_SECONDS": "0.25",
                "MINICLAW_LLM_RETRY_MAX_SECONDS": "2",
                "MINICLAW_LLM_RETRY_JITTER_RATIO": "0",
                "MINICLAW_LLM_FALLBACKS": "primary:backup-model,deepseek:deepseek-chat",
            }
        )
        self.assertEqual(settings.timeout_seconds, 90)
        self.assertEqual(settings.connect_timeout_seconds, 5)
        self.assertEqual(settings.first_token_timeout_seconds, 20)
        self.assertEqual(settings.idle_timeout_seconds, 15)
        self.assertEqual(settings.max_retries, 0)
        self.assertEqual(
            [(route.provider, route.model_id) for route in settings.fallbacks],
            [("primary", "backup-model"), ("deepseek", "deepseek-chat")],
        )


if __name__ == "__main__":
    unittest.main()
