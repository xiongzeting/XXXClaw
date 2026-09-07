from __future__ import annotations

import unittest

from MiniClaw.llm.types import ModelProfile
from MiniClaw.coding_agent.memory.config import load_memory_config


class MemoryConfigTests(unittest.TestCase):
    def test_default_profile_uses_evaluation_watermarks(self) -> None:
        config = load_memory_config(ModelProfile("fake"), environment={})
        self.assertEqual(config.target_tokens, 30_000)
        self.assertEqual(config.keep_recent_tokens, 20_000)
        self.assertEqual(config.soft_trigger_tokens, 80_000)
        self.assertEqual(config.hard_trigger_tokens, 100_000)
        self.assertEqual(config.reserve_tokens, 12_800)
        self.assertEqual(config.deterministic_semantic_tokens, 4_500)

    def test_large_profile_uses_scaled_reference_watermarks(self) -> None:
        config = load_memory_config(
            ModelProfile("fake", context_window=1_000_000, max_output_tokens=16_000),
            environment={},
        )
        self.assertEqual(config.target_tokens, 30_000)
        self.assertEqual(config.keep_recent_tokens, 20_000)
        self.assertEqual(config.soft_trigger_tokens, 80_000)
        self.assertEqual(config.hard_trigger_tokens, 100_000)
        self.assertEqual(config.reserve_tokens, 16_384)

    def test_small_window_scales_down_safely(self) -> None:
        config = load_memory_config(
            ModelProfile("small", context_window=64_000, max_output_tokens=4_000),
            environment={},
        )
        self.assertLess(config.keep_recent_tokens, config.target_tokens)
        self.assertLess(config.target_tokens, config.soft_trigger_tokens)
        self.assertLess(config.soft_trigger_tokens, config.hard_trigger_tokens)
        self.assertLessEqual(
            config.hard_trigger_tokens,
            64_000 - config.reserve_tokens,
        )

    def test_explicit_impossible_target_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "TARGET_TOKENS"):
            load_memory_config(
                ModelProfile("small", context_window=64_000, max_output_tokens=4_000),
                environment={"MINICLAW_COMPACTION_TARGET_TOKENS": "100000"},
            )


if __name__ == "__main__":
    unittest.main()
