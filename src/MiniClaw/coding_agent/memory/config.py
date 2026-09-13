from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from MiniClaw.llm.types import ModelProfile


# Production progressive-compaction profile derived from the pi-style design.
# Smaller model windows keep the same proportions and trigger ordering automatically.
REFERENCE_TARGET_TOKENS = 30_000
REFERENCE_KEEP_RECENT_TOKENS = 10_000
# These are single-request provider input watermarks.  The cumulative ledger
# is retained only for diagnostics and cost reporting; it does not trigger
# compaction.
REFERENCE_HARD_TRIGGER_TOKENS = 40_000
REFERENCE_RESERVE_TOKENS = 16_384
REFERENCE_SEMANTIC_TOKENS = 5_000
# Explicit user-facing spill threshold: 8 KiB of UTF-8 tool output.
# The complete payload is still recoverable from the artifact file; only the
# compact reference is kept in the model-visible transcript.
REFERENCE_LARGE_TOOL_BYTES = 8 * 1024

# ``strategy`` is retained in the serialized configuration for old benchmark
# files, but runtime compaction is intentionally one progressive policy.  The
# old names are migration labels, not separate production algorithms.
CompactionStrategy = Literal["layered-current", "legacy-summary-recent", "progressive"]
ACTIVE_COMPACTION_POLICY = "progressive"


@dataclass(slots=True, frozen=True)
class MemoryConfig:
    enabled: bool
    reserve_tokens: int
    keep_recent_tokens: int
    hard_trigger_tokens: int
    target_tokens: int
    # Kept for loading older session configurations.  WorkingContext now
    # always uses the progressive pipeline; this flag no longer selects a
    # second runtime algorithm.
    progressive_enabled: bool
    artifact_threshold_bytes: int
    artifact_preview_chars: int
    deterministic_semantic_tokens: int
    strategy: CompactionStrategy = "progressive"


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
    # Hard pressure is measured against the current provider request. The
    # cumulative ledger remains available for diagnostics and cost reporting.
    default_hard = REFERENCE_HARD_TRIGGER_TOKENS
    hard = _integer(env, "MINICLAW_COMPACTION_HARD_TRIGGER_TOKENS", default_hard, errors)
    default_target = min(
        REFERENCE_TARGET_TOKENS,
        max(1, int(default_hard * 0.3)),
    )
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
    requested_artifact_threshold = _integer(
        env,
        "MINICLAW_CONTEXT_ARTIFACT_THRESHOLD_BYTES",
        REFERENCE_LARGE_TOOL_BYTES,
        errors,
    )
    # The immediate-spill guarantee is a production invariant.  An
    # environment override may raise the threshold, but may not lower it
    # below the 1K-token boundary.
    artifact_threshold = max(requested_artifact_threshold, REFERENCE_LARGE_TOOL_BYTES)
    artifact_preview = _integer(
        env,
        "MINICLAW_CONTEXT_ARTIFACT_PREVIEW_CHARS",
        # Keep the live reference short. The complete payload remains on disk;
        # the model receives only a compact orientation summary.
        256,
        errors,
    )
    semantic_budget = _integer(
        env,
        "MINICLAW_CONTEXT_DETERMINISTIC_SEMANTIC_TOKENS",
        min(REFERENCE_SEMANTIC_TOKENS, max(1, int(target * 0.15))),
        errors,
    )
    enabled = _boolean(env, "MINICLAW_COMPACTION_ENABLED", True, errors)
    progressive = _boolean(env, "MINICLAW_PROGRESSIVE_COMPACTION_ENABLED", True, errors)
    strategy = env.get("MINICLAW_COMPACTION_STRATEGY", "progressive").strip().lower()
    if strategy not in {"layered-current", "legacy-summary-recent", "progressive"}:
        errors.append(
            "MINICLAW_COMPACTION_STRATEGY must be progressive "
            "(legacy labels are accepted only for historical replay)"
        )

    if reserve < profile.max_output_tokens:
        errors.append("MINICLAW_COMPACTION_RESERVE_TOKENS must cover the model max output tokens")
    if reserve >= profile.context_window:
        errors.append("MINICLAW_COMPACTION_RESERVE_TOKENS must be below the context window")
    if target >= hard:
        errors.append("MINICLAW_COMPACTION_TARGET_TOKENS must be below the hard trigger")
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
        hard_trigger_tokens=hard,
        target_tokens=target,
        progressive_enabled=progressive,
        artifact_threshold_bytes=artifact_threshold,
        artifact_preview_chars=artifact_preview,
        deterministic_semantic_tokens=semantic_budget,
        strategy=strategy,  # type: ignore[arg-type]
    )
