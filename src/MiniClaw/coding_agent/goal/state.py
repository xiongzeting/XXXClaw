from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .config import GoalConfig


GoalStatus = Literal[
    "active",
    "verifying",
    "complete",
    "waiting_for_user",
    "failed",
    "cancelled",
]


@dataclass(slots=True)
class GoalCheckpoint:
    at: str
    summary: str
    source: Literal["agent", "automatic"]


@dataclass(slots=True)
class GoalVerification:
    id: str
    command: str
    started_at: str
    completed_at: str | None = None
    passed: bool | None = None
    exit_code: int | None = None
    output: str | None = None
    error: str | None = None


@dataclass(slots=True)
class GoalCriterionEvidence:
    criterion: str
    evidence: str


@dataclass(slots=True)
class GoalState:
    version: int
    goal: str
    status: GoalStatus
    acceptance_criteria: list[str]
    attempt_count: int
    created_at: str
    updated_at: str
    last_checkpoint: GoalCheckpoint | None
    final_result: str | None
    limits: GoalConfig
    spent_cost_usd: float
    current_attempt_started_at: str | None
    last_verification: GoalVerification | None
    verification_history: list[GoalVerification] = field(default_factory=list)
    last_judge_rejection: GoalCheckpoint | None = None
    criteria_evidence: list[GoalCriterionEvidence] = field(default_factory=list)
    requested_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["limits"] = self.limits.to_dict()
        return value

    @classmethod
    def from_dict(cls, value: object) -> "GoalState":
        if not isinstance(value, dict):
            raise ValueError("[INVALID_GOAL_STATE] Goal file is not an object")
        statuses = {
            "active",
            "verifying",
            "complete",
            "waiting_for_user",
            "failed",
            "cancelled",
        }
        if value.get("version") != 1 or value.get("status") not in statuses:
            raise ValueError("[INVALID_GOAL_STATE] Goal file has an invalid version or status")
        goal = value.get("goal")
        criteria = value.get("acceptance_criteria")
        if not isinstance(goal, str) or not isinstance(criteria, list) or not all(
            isinstance(item, str) for item in criteria
        ):
            raise ValueError("[INVALID_GOAL_STATE] Goal text or criteria are invalid")
        attempt_count = value.get("attempt_count")
        if not isinstance(attempt_count, int) or isinstance(attempt_count, bool) or attempt_count < 0:
            raise ValueError("[INVALID_GOAL_STATE] attempt_count is invalid")
        return cls(
            version=1,
            goal=goal,
            status=value["status"],
            acceptance_criteria=list(criteria),
            attempt_count=attempt_count,
            created_at=_required_string(value, "created_at"),
            updated_at=_required_string(value, "updated_at"),
            last_checkpoint=_checkpoint(value.get("last_checkpoint")),
            final_result=_optional_string(value.get("final_result")),
            requested_by=_optional_string(value.get("requested_by")),
            limits=GoalConfig.from_dict(value.get("limits")),
            spent_cost_usd=float(value.get("spent_cost_usd", 0)),
            current_attempt_started_at=_optional_string(value.get("current_attempt_started_at")),
            last_verification=_verification(value.get("last_verification")),
            verification_history=[
                item
                for raw in value.get("verification_history", [])
                if (item := _verification(raw)) is not None
            ],
            last_judge_rejection=_checkpoint(value.get("last_judge_rejection")),
            criteria_evidence=[
                GoalCriterionEvidence(
                    criterion=_required_string(raw, "criterion"),
                    evidence=_required_string(raw, "evidence"),
                )
                for raw in value.get("criteria_evidence", [])
                if isinstance(raw, dict)
            ],
        )


def _required_string(value: dict[str, Any], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str):
        raise ValueError(f"[INVALID_GOAL_STATE] {name} is invalid")
    return item


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _checkpoint(value: object) -> GoalCheckpoint | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("[INVALID_GOAL_STATE] checkpoint is invalid")
    source = value.get("source")
    if source not in {"agent", "automatic"}:
        raise ValueError("[INVALID_GOAL_STATE] checkpoint source is invalid")
    return GoalCheckpoint(
        at=_required_string(value, "at"),
        summary=_required_string(value, "summary"),
        source=source,
    )


def _verification(value: object) -> GoalVerification | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("[INVALID_GOAL_STATE] verification is invalid")
    passed = value.get("passed")
    if passed is not None and not isinstance(passed, bool):
        raise ValueError("[INVALID_GOAL_STATE] verification.passed is invalid")
    exit_code = value.get("exit_code")
    if exit_code is not None and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
        raise ValueError("[INVALID_GOAL_STATE] verification.exit_code is invalid")
    return GoalVerification(
        id=_required_string(value, "id"),
        command=_required_string(value, "command"),
        started_at=_required_string(value, "started_at"),
        completed_at=_optional_string(value.get("completed_at")),
        passed=passed,
        exit_code=exit_code,
        output=_optional_string(value.get("output")),
        error=_optional_string(value.get("error")),
    )
