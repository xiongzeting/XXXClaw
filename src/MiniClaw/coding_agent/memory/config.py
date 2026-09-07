from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from MiniClaw.llm.types import ModelProfile


# Production progressive-compaction profile derived from the pi-style design.
# Smaller model windows keep the same proportions and trigger ordering automatically.
REFERENCE_TARGET_TOKENS = 30_000
REFERENCE_KEEP_RECENT_TOKENS = 20_000
REFERENCE_SOFT_TRIGGER_TOKENS = 80_000
REFERENCE_HARD_TRIGGER_TOKENS = 100_000
REFERENCE_RESERVE_TOKENS = 16_384
REFERENCE_SEMANTIC_TOKENS = 4_500

CompactionStrategy = Literal["layered-current", "legacy-summary-recent"]


@dataclass(slots=True, frozen=True)
class MemoryConfig:
    enabled: bool
    reserve_tokens: int
    keep_recent_tokens: int
    soft_trigger_tokens: int
    hard_trigger_tokens: int
    target_tokens: int
    progressive_enabled: bool
    artifact_threshold_bytes: int
    artifact_preview_chars: int
    deterministic_semantic_tokens: int
    strategy: CompactionStrategy = "layered-current"


def _integer(env: Mapping[str, str], name: str, fallback: int, errors: list[str]) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return fallback
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{name} must be a positive integer")
        return fallback
    if value <= 0:
        errors.append(f"{name} must be a positive integer")
        return fallback
    return value


def _boolean(env: Mapping[str, str], name: str, fallback: bool, errors: list[str]) -> bool:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return fallback
    normalized = raw.strip().casefold()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    errors.append(f"{name} must be true or false")
    return fallback


def load_memory_config(
    profile: ModelProfile,
    environment: Mapping[str, str] | None = None,
) -> MemoryConfig:
    env = os.environ if environment is None else environment
    errors: list[str] = []
    default_reserve = max(
        profile.max_output_tokens,
        min(REFERENCE_RESERVE_TOKENS, max(1, profile.context_window // 10)),
    )
    reserve = _integer(env, "MINICLAW_COMPACTION_RESERVE_TOKENS", default_reserve, errors)
    available = max(1, profile.context_window - reserve)
    default_hard = min(REFERENCE_HARD_TRIGGER_TOKENS, available)
    hard = _integer(env, "MINICLAW_COMPACTION_HARD_TRIGGER_TOKENS", default_hard, errors)
    default_target = min(
        REFERENCE_TARGET_TOKENS,
        max(1, int(default_hard * 0.3)),
    )
    default_soft = min(
        REFERENCE_SOFT_TRIGGER_TOKENS,
        max(
            default_target + 1,
            default_target + (default_hard - default_target) // 2,
            int(default_hard * 0.8),
        ),
    )
    soft = _integer(env, "MINICLAW_COMPACTION_SOFT_TRIGGER_TOKENS", default_soft, errors)
    target = _integer(
        env,
        "MINICLAW_COMPACTION_TARGET_TOKENS",
        default_target,
        errors,
    )
    keep = _integer(
        env,
        "MINICLAW_COMPACTION_KEEP_RECENT_TOKENS",
        min(REFERENCE_KEEP_RECENT_TOKENS, max(1, int(target * (2 / 3)))),
        errors,
    )
    artifact_threshold = _integer(
        env, "MINICLAW_CONTEXT_ARTIFACT_THRESHOLD_BYTES", 16 * 1024, errors
    )
    artifact_preview = _integer(env, "MINICLAW_CONTEXT_ARTIFACT_PREVIEW_CHARS", 4_000, errors)
    semantic_budget = _integer(
        env,
        "MINICLAW_CONTEXT_DETERMINISTIC_SEMANTIC_TOKENS",
        min(REFERENCE_SEMANTIC_TOKENS, max(1, int(target * 0.15))),
        errors,
    )
    enabled = _boolean(env, "MINICLAW_COMPACTION_ENABLED", True, errors)
    progressive = _boolean(env, "MINICLAW_PROGRESSIVE_COMPACTION_ENABLED", True, errors)
    strategy = env.get("MINICLAW_COMPACTION_STRATEGY", "layered-current").strip().lower()
    if strategy not in {"layered-current", "legacy-summary-recent"}:
        errors.append(
            "MINICLAW_COMPACTION_STRATEGY must be layered-current or legacy-summary-recent"
        )

    if reserve < profile.max_output_tokens:
        errors.append("MINICLAW_COMPACTION_RESERVE_TOKENS must cover the model max output tokens")
    if reserve >= profile.context_window:
        errors.append("MINICLAW_COMPACTION_RESERVE_TOKENS must be below the context window")
    if hard > profile.context_window - reserve:
        errors.append("MINICLAW_COMPACTION_HARD_TRIGGER_TOKENS must leave the reserve available")
    if soft >= hard:
        errors.append("MINICLAW_COMPACTION_SOFT_TRIGGER_TOKENS must be below the hard trigger")
    if target >= soft:
        errors.append("MINICLAW_COMPACTION_TARGET_TOKENS must be below the soft trigger")
    if keep >= target:
        errors.append("MINICLAW_COMPACTION_KEEP_RECENT_TOKENS must be below the target")
    if artifact_threshold < 1_024:
        errors.append("MINICLAW_CONTEXT_ARTIFACT_THRESHOLD_BYTES must be at least 1024")
    if artifact_preview >= artifact_threshold:
        errors.append("MINICLAW_CONTEXT_ARTIFACT_PREVIEW_CHARS must be below the artifact threshold")
    if semantic_budget >= hard:
        errors.append("MINICLAW_CONTEXT_DETERMINISTIC_SEMANTIC_TOKENS must be below the hard trigger")
    if errors:
        raise ValueError("Invalid memory configuration:\n- " + "\n- ".join(errors))

    return MemoryConfig(
        enabled=enabled,
        reserve_tokens=reserve,
        keep_recent_tokens=keep,
        soft_trigger_tokens=soft,
        hard_trigger_tokens=hard,
        target_tokens=target,
        progressive_enabled=progressive,
        artifact_threshold_bytes=artifact_threshold,
        artifact_preview_chars=artifact_preview,
        deterministic_semantic_tokens=semantic_budget,
        strategy=strategy,  # type: ignore[arg-type]
    )
