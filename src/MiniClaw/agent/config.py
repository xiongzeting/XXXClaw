from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class AgentBudget:
    """Runtime safety budgets for one agent attempt.

    These are deliberately not turn limits: a turn is an implementation detail,
    while token, wall-clock, and observable progress are the actual resources.
    """

    token_budget: int = 1_000_000
    time_budget_seconds: float = 3_600.0
    no_progress_limit: int = 6


def load_agent_budget(environment: Mapping[str, str] | None = None) -> AgentBudget:
    env = os.environ if environment is None else environment
    return AgentBudget(
        token_budget=_positive_int(env.get("MINICLAW_AGENT_TOKEN_BUDGET"), 1_000_000),
        time_budget_seconds=_positive_float(
            env.get("MINICLAW_AGENT_TIME_BUDGET_SECONDS"), 3_600.0
        ),
        no_progress_limit=_positive_int(
            env.get("MINICLAW_AGENT_NO_PROGRESS_LIMIT"), 6
        ),
    )


def _positive_int(raw: str | None, fallback: int) -> int:
    try:
        value = int(raw) if raw is not None else fallback
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def _positive_float(raw: str | None, fallback: float) -> float:
    try:
        value = float(raw) if raw is not None else fallback
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback
