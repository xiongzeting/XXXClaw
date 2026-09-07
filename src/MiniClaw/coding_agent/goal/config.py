from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(slots=True, frozen=True)
class GoalConfig:
    max_attempts: int = 20
    max_duration_seconds: int = 4 * 60 * 60
    max_cost_usd: float = 10.0

    def to_dict(self) -> dict[str, int | float]:
        return {
            "maxAttempts": self.max_attempts,
            "maxDurationMs": self.max_duration_seconds * 1000,
            "maxCostUsd": self.max_cost_usd,
        }

    @classmethod
    def from_dict(cls, value: object) -> "GoalConfig":
        if not isinstance(value, dict):
            raise ValueError("[INVALID_GOAL_STATE] limits must be an object")
        return cls(
            max_attempts=_positive_int(value.get("maxAttempts"), "limits.maxAttempts"),
            max_duration_seconds=_positive_int(
                max(1, int(value.get("maxDurationMs", 0)) // 1000),
                "limits.maxDurationMs",
            ),
            max_cost_usd=_non_negative_float(value.get("maxCostUsd"), "limits.maxCostUsd"),
        )


@dataclass(slots=True, frozen=True)
class GoalJudgeConfig:
    enabled: bool = False
    model_id: str | None = None
    timeout_seconds: float = 60.0


def load_goal_config(environment: Mapping[str, str] | None = None) -> GoalConfig:
    env = environment if environment is not None else os.environ
    attempts = _first(env, "MINICLAW_GOAL_MAX_ATTEMPTS")
    duration_seconds = _first(env, "MINICLAW_GOAL_MAX_DURATION_SECONDS")
    cost = _first(env, "MINICLAW_GOAL_MAX_COST_USD")
    return GoalConfig(
        max_attempts=_positive_int(attempts, "MINICLAW_GOAL_MAX_ATTEMPTS", 20),
        max_duration_seconds=_positive_int(
            duration_seconds,
            "MINICLAW_GOAL_MAX_DURATION_SECONDS",
            14_400,
        ),
        max_cost_usd=_non_negative_float(cost, "MINICLAW_GOAL_MAX_COST_USD", 10.0),
    )


def load_goal_judge_config(environment: Mapping[str, str] | None = None) -> GoalJudgeConfig:
    env = environment if environment is not None else os.environ
    enabled = _boolean(
        _first(env, "MINICLAW_GOAL_JUDGE_ENABLED"),
        False,
        "MINICLAW_GOAL_JUDGE_ENABLED",
    )
    model_id = _first(env, "MINICLAW_GOAL_JUDGE_MODEL")
    timeout_seconds = _first(env, "MINICLAW_GOAL_JUDGE_TIMEOUT")
    timeout = _positive_float(
        timeout_seconds,
        "MINICLAW_GOAL_JUDGE_TIMEOUT",
        60.0,
    )
    return GoalJudgeConfig(enabled=enabled, model_id=model_id, timeout_seconds=timeout)


def _first(environment: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = environment.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _positive_int(value: object, name: str, default: int | None = None) -> int:
    if value is None and default is not None:
        return default
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _positive_float(value: object, name: str, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    try:
        parsed = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive number")
    return parsed


def _non_negative_float(value: object, name: str, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    try:
        parsed = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative number") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be a non-negative number")
    return parsed


def _boolean(value: str | None, default: bool, name: str) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")
