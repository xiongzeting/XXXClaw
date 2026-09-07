from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from MiniClaw.coding_agent.memory.locking import MemoryFileLock

from .config import GoalConfig
from .state import (
    GoalCheckpoint,
    GoalCriterionEvidence,
    GoalState,
    GoalVerification,
)


MAX_GOAL_FILE_BYTES = 128 * 1024
MAX_GOAL_TEXT_CHARS = 8_000
MAX_CHECKPOINT_CHARS = 8_000
MAX_VERIFICATION_OUTPUT_CHARS = 16_000
MAX_VERIFICATION_HISTORY = 4


@dataclass(slots=True)
class GoalAttemptDecision:
    allowed: bool
    state: GoalState
    reason: str | None = None


@dataclass(slots=True)
class GoalCompletionCandidate:
    state: GoalState
    final_result: str
    criteria_evidence: list[GoalCriterionEvidence]


@dataclass(slots=True)
class GoalVerificationBundle:
    completed: list[GoalVerification]
    pending: list[GoalVerification]


class GoalStore:
    def __init__(
        self,
        session_dir: str | Path,
        config: GoalConfig | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(session_dir) / "goal.json"
        self.config = config or GoalConfig()
        self._now = now or (lambda: datetime.now(timezone.utc))

    def read(self) -> GoalState | None:
        if not self.path.exists():
            return None
        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError(f"[UNSAFE_GOAL_PATH] Goal path must be a regular file: {self.path}")
        if self.path.stat().st_size > MAX_GOAL_FILE_BYTES:
            raise ValueError(f"[GOAL_TOO_LARGE] Goal file exceeds {MAX_GOAL_FILE_BYTES} bytes")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("[INVALID_GOAL_STATE] Goal file is not valid JSON") from exc
        return GoalState.from_dict(value)

    def create(
        self,
        goal: str,
        acceptance_criteria: list[str],
        requested_by: str | None = None,
    ) -> GoalState:
        normalized_goal = _normalize_text(goal, "goal", MAX_GOAL_TEXT_CHARS)
        criteria = _normalize_criteria(acceptance_criteria, normalized_goal)

        def operation(_: GoalState | None) -> GoalState:
            now = self._timestamp()
            return GoalState(
                version=1,
                goal=normalized_goal,
                status="active",
                acceptance_criteria=criteria,
                attempt_count=0,
                created_at=now,
                updated_at=now,
                last_checkpoint=None,
                final_result=None,
                requested_by=requested_by,
                limits=self.config,
                spent_cost_usd=0.0,
                current_attempt_started_at=None,
                last_verification=None,
                verification_history=[],
                last_judge_rejection=None,
                criteria_evidence=[],
            )

        return self._mutate(operation)

    def begin_attempt(self) -> GoalAttemptDecision:
        decision: GoalAttemptDecision | None = None

        def operation(state: GoalState | None) -> GoalState:
            nonlocal decision
            current = _require_goal(state)
            if current.status not in {"active", "verifying"}:
                decision = GoalAttemptDecision(False, current, f"Goal status is {current.status}")
                return current
            limit_reason = self._limit_reason(current)
            if limit_reason:
                failed = self._fail_state(current, limit_reason)
                decision = GoalAttemptDecision(False, failed, limit_reason)
                return failed
            current.updated_at = self._timestamp()
            current.attempt_count += 1
            current.current_attempt_started_at = current.updated_at
            decision = GoalAttemptDecision(True, current)
            return current

        self._mutate(operation)
        if decision is None:
            raise RuntimeError("Goal attempt decision was not created")
        return decision

    def finish_attempt(self, cost_usd: float, automatic_checkpoint: str | None = None) -> GoalState:
        def operation(state: GoalState | None) -> GoalState:
            current = _require_goal(state)
            current.updated_at = self._timestamp()
            current.spent_cost_usd = round(current.spent_cost_usd + max(0.0, cost_usd), 6)
            if (
                current.status == "active"
                and automatic_checkpoint
                and automatic_checkpoint.strip()
                and (
                    current.last_checkpoint is None
                    or current.current_attempt_started_at is None
                    or current.last_checkpoint.at < current.current_attempt_started_at
                )
            ):
                current.last_checkpoint = GoalCheckpoint(
                    at=current.updated_at,
                    summary=_truncate(automatic_checkpoint.strip(), MAX_CHECKPOINT_CHARS),
                    source="automatic",
                )
            current.current_attempt_started_at = None
            reason = self._limit_reason(current)
            if reason and current.status in {"active", "verifying"}:
                return self._fail_state(current, reason)
            return current

        return self._mutate(operation)

    def checkpoint(self, summary: str) -> GoalState:
        normalized = _normalize_text(summary, "checkpoint", MAX_CHECKPOINT_CHARS)

        def operation(state: GoalState | None) -> GoalState:
            current = _require_runnable(state)
            current.updated_at = self._timestamp()
            current.status = "active"
            current.last_checkpoint = GoalCheckpoint(current.updated_at, normalized, "agent")
            return current

        return self._mutate(operation)

    def invalidate_verification(self, reason: str) -> GoalState | None:
        normalized = _normalize_text(reason, "verification invalidation reason", MAX_CHECKPOINT_CHARS)
        with MemoryFileLock(self.path):
            state = self.read()
            if (
                state is None
                or state.status not in {"active", "verifying"}
                or (state.last_verification is None and not state.verification_history)
            ):
                return state
            state.updated_at = self._timestamp()
            state.status = "active"
            state.last_verification = None
            state.verification_history = []
            state.last_checkpoint = GoalCheckpoint(
                state.updated_at,
                f"Verification invalidated: {normalized}",
                "automatic",
            )
            self._persist(state)
            return state

    def start_verification(self, verification_id: str, command: str) -> GoalState:
        normalized = _normalize_text(command, "verification command", MAX_GOAL_TEXT_CHARS)

        def operation(state: GoalState | None) -> GoalState:
            current = _require_runnable(state)
            current.updated_at = self._timestamp()
            current.status = "verifying"
            verification = GoalVerification(
                id=verification_id,
                command=normalized,
                started_at=current.updated_at,
            )
            current.last_verification = verification
            current.verification_history = [*current.verification_history, verification][
                -MAX_VERIFICATION_HISTORY:
            ]
            return current

        return self._mutate(operation)

    def finish_verification(
        self,
        verification_id: str,
        *,
        passed: bool,
        exit_code: int | None = None,
        output: str | None = None,
        error: str | None = None,
    ) -> GoalState:
        def operation(state: GoalState | None) -> GoalState:
            current = _require_runnable(state)
            index = next(
                (
                    item_index
                    for item_index, verification in enumerate(current.verification_history)
                    if verification.id == verification_id
                ),
                None,
            )
            if index is None:
                raise ValueError("[GOAL_VERIFICATION_MISMATCH] No matching verification command is active")
            current.updated_at = self._timestamp()
            verification = current.verification_history[index]
            verification.completed_at = current.updated_at
            verification.passed = passed
            verification.exit_code = exit_code
            verification.output = _truncate_tail(output, MAX_VERIFICATION_OUTPUT_CHARS) if output else None
            verification.error = _truncate_tail(error, MAX_VERIFICATION_OUTPUT_CHARS) if error else None
            current.last_verification = verification
            current.status = "verifying" if passed else "active"
            return current

        return self._mutate(operation)

    def verification_bundle(self, state: GoalState | None = None) -> GoalVerificationBundle:
        current = state or _require_goal(self.read())
        history = current.verification_history or (
            [current.last_verification] if current.last_verification else []
        )
        checkpoint_at = current.last_checkpoint.at if current.last_checkpoint else ""
        after_checkpoint = [item for item in history if item.started_at >= checkpoint_at]
        failed_times = [
            item.completed_at or ""
            for item in after_checkpoint
            if item.completed_at and item.passed is False
        ]
        latest_failure_at = max(failed_times, default="")
        fresh = [item for item in after_checkpoint if item.started_at >= latest_failure_at]
        return GoalVerificationBundle(
            completed=[
                item
                for item in fresh
                if item.completed_at is not None and item.passed is True and item.exit_code == 0
            ],
            pending=[item for item in fresh if item.completed_at is None],
        )

    def validate_completion(
        self,
        final_result: str,
        criteria_evidence: list[GoalCriterionEvidence],
    ) -> GoalCompletionCandidate:
        current = _require_runnable(self.read())
        if current.status != "verifying":
            raise ValueError("[GOAL_NOT_VERIFYING] Run a real verification command before goal_complete")
        bundle = self.verification_bundle(current)
        if bundle.pending:
            raise ValueError("[GOAL_VERIFICATION_RUNNING] Wait for every verification command to finish")
        if not bundle.completed:
            raise ValueError(
                "[GOAL_VERIFICATION_REQUIRED] At least one fresh verification command must finish with exit code 0"
            )
        evidence = _validate_criteria_evidence(current.acceptance_criteria, criteria_evidence)
        verification_output = "\n\n".join(
            f"$ {item.command}\n{item.output or '(no output)'}" for item in bundle.completed
        )
        _assert_objective_evidence(current.goal, current.acceptance_criteria, verification_output)
        return GoalCompletionCandidate(
            state=current,
            final_result=_normalize_text(final_result, "final result", MAX_GOAL_TEXT_CHARS),
            criteria_evidence=evidence,
        )

    def submit_completion(self, final_result: str) -> GoalState:
        """Stop goal execution on the model's submission; this is not a judge verdict."""
        normalized = _normalize_text(final_result, "final result", MAX_GOAL_TEXT_CHARS)
        def operation(state):
            current = _require_runnable(state)
            current.updated_at = self._timestamp()
            current.status = 'complete'
            current.current_attempt_started_at = None
            current.final_result = normalized
            current.criteria_evidence = []
            return current
        return self._mutate(operation)

    def complete(
        self,
        final_result: str,
        criteria_evidence: list[GoalCriterionEvidence],
    ) -> GoalState:
        candidate = self.validate_completion(final_result, criteria_evidence)

        def operation(state: GoalState | None) -> GoalState:
            current = _require_runnable(state)
            if current.updated_at != candidate.state.updated_at:
                raise ValueError("[GOAL_CHANGED] Goal changed while completion was being reviewed")
            current.updated_at = self._timestamp()
            current.status = "complete"
            current.current_attempt_started_at = None
            current.final_result = candidate.final_result
            current.criteria_evidence = candidate.criteria_evidence
            return current

        return self._mutate(operation)

    def reject_completion(self, reason: str) -> GoalState:
        normalized = _normalize_text(reason, "judge rejection reason", MAX_CHECKPOINT_CHARS)

        def operation(state: GoalState | None) -> GoalState:
            current = _require_runnable(state)
            current.updated_at = self._timestamp()
            current.status = "verifying"
            current.current_attempt_started_at = None
            current.final_result = None
            current.criteria_evidence = []
            current.last_judge_rejection = GoalCheckpoint(
                current.updated_at, normalized, "automatic"
            )
            return current

        return self._mutate(operation)

    def wait_for_user(self, reason: str) -> GoalState:
        normalized = _normalize_text(reason, "waiting reason", MAX_CHECKPOINT_CHARS)

        def operation(state: GoalState | None) -> GoalState:
            current = _require_runnable(state)
            current.updated_at = self._timestamp()
            current.status = "waiting_for_user"
            current.current_attempt_started_at = None
            current.last_checkpoint = GoalCheckpoint(current.updated_at, normalized, "agent")
            return current

        return self._mutate(operation)

    def fail(self, reason: str) -> GoalState:
        normalized = _normalize_text(reason, "failure reason", MAX_GOAL_TEXT_CHARS)
        return self._mutate(lambda state: self._fail_state(_require_goal(state), normalized))

    def cancel(self, reason: str) -> GoalState:
        normalized = _normalize_text(reason, "cancellation reason", MAX_GOAL_TEXT_CHARS)

        def operation(state: GoalState | None) -> GoalState:
            current = _require_goal(state)
            if current.status == "cancelled":
                return current
            if current.status not in {"active", "verifying", "waiting_for_user"}:
                raise ValueError(f"[GOAL_NOT_CANCELLABLE] Goal status is {current.status}")
            current.updated_at = self._timestamp()
            current.status = "cancelled"
            current.current_attempt_started_at = None
            current.final_result = normalized
            return current

        return self._mutate(operation)

    def resume(self) -> GoalState:
        def operation(state: GoalState | None) -> GoalState:
            current = _require_goal(state)
            if current.status != "waiting_for_user":
                raise ValueError(
                    f"[GOAL_NOT_WAITING] Goal status is {current.status}, not waiting_for_user"
                )
            current.updated_at = self._timestamp()
            current.status = "active"
            current.current_attempt_started_at = None
            return current

        return self._mutate(operation)

    def _limit_reason(self, state: GoalState) -> str | None:
        if state.attempt_count >= state.limits.max_attempts:
            return f"Goal stopped after reaching the attempt limit ({state.limits.max_attempts})"
        created = datetime.fromisoformat(state.created_at.replace("Z", "+00:00"))
        current_time = self._now()
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        if (current_time.astimezone(timezone.utc) - created).total_seconds() >= state.limits.max_duration_seconds:
            return (
                "Goal stopped after reaching the time limit "
                f"({state.limits.max_duration_seconds * 1000}ms)"
            )
        if state.limits.max_cost_usd > 0 and state.spent_cost_usd >= state.limits.max_cost_usd:
            return f"Goal stopped after reaching the cost limit (${state.limits.max_cost_usd:.2f})"
        return None

    def _fail_state(self, state: GoalState, reason: str) -> GoalState:
        state.updated_at = self._timestamp()
        state.status = "failed"
        state.current_attempt_started_at = None
        state.final_result = reason
        return state

    def _timestamp(self) -> str:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _mutate(self, operation: Callable[[GoalState | None], GoalState]) -> GoalState:
        with MemoryFileLock(self.path):
            state = operation(self.read())
            self._persist(state)
            return state

    def _persist(self, state: GoalState) -> None:
        content = json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n"
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_GOAL_FILE_BYTES:
            raise ValueError(f"[GOAL_TOO_LARGE] Goal state exceeds {MAX_GOAL_FILE_BYTES} bytes")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


def _require_goal(state: GoalState | None) -> GoalState:
    if state is None:
        raise ValueError("[NO_GOAL] No goal exists for this conversation")
    return GoalState.from_dict(state.to_dict())


def _require_runnable(state: GoalState | None) -> GoalState:
    current = _require_goal(state)
    if current.status not in {"active", "verifying"}:
        raise ValueError(f"[GOAL_NOT_ACTIVE] Goal status is {current.status}")
    return current


def _normalize_text(value: str, name: str, max_chars: int) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"[EMPTY_GOAL_FIELD] {name} cannot be empty")
    if len(normalized) > max_chars:
        raise ValueError(f"[GOAL_FIELD_TOO_LARGE] {name} exceeds {max_chars} characters")
    return normalized


def _normalize_criteria(criteria: list[str], fallback: str) -> list[str]:
    normalized = [_normalize_text(item, "acceptance criterion", 2_000) for item in criteria]
    unique = list(dict.fromkeys(normalized or [fallback]))
    return unique[:20]


def _validate_criteria_evidence(
    criteria: list[str], evidence: list[GoalCriterionEvidence]
) -> list[GoalCriterionEvidence]:
    normalized = [
        GoalCriterionEvidence(
            criterion=_normalize_text(item.criterion, "criterion evidence name", 2_000),
            evidence=_normalize_text(item.evidence, "criterion evidence", 4_000),
        )
        for item in evidence
    ]
    names = [item.criterion for item in normalized]
    if len(names) != len(criteria) or sorted(names) != sorted(criteria) or len(set(names)) != len(names):
        skeleton = [
            {"criterion": criterion, "evidence": "replace with concrete verification evidence"}
            for criterion in criteria
        ]
        raise ValueError(
            "[GOAL_CRITERION_UNVERIFIED] criteria_evidence must contain every exact criterion "
            f"once, without translation or paraphrase. Required skeleton: {json.dumps(skeleton, ensure_ascii=False)}"
        )
    by_name = {item.criterion: item for item in normalized}
    return [by_name[criterion] for criterion in criteria]


def _truncate(value: str, max_chars: int) -> str:
    return value if len(value) <= max_chars else f"{value[:max_chars]}\n[truncated]"


def _truncate_tail(value: str, max_chars: int) -> str:
    return value if len(value) <= max_chars else f"[earlier output truncated]\n{value[-max_chars:]}"


def _assert_objective_evidence(goal: str, criteria: list[str], output: str) -> None:
    text = "\n".join([goal, *criteria])
    patterns = [
        r"覆盖率[^\n]{0,40}?(?:达到|至少|不少于|不低于|>=|≥)\s*(\d+(?:\.\d+)?)\s*%",
        r"coverage[^\n]{0,40}?(?:at least|no less than|>=|≥|reach(?:es)?|to)\s*(\d+(?:\.\d+)?)\s*%",
        r"(\d+(?:\.\d+)?)\s*%[^\n]{0,30}?coverage",
    ]
    target: float | None = None
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            target = float(match.group(1))
            break
    if target is None:
        return
    observed: float | None = None
    total_lines = [line for line in output.splitlines() if re.match(r"^\s*TOTAL\b", line, re.I)]
    if total_lines:
        percentages = re.findall(r"(\d+(?:\.\d+)?)\s*%", total_lines[-1])
        if percentages:
            observed = float(percentages[-1])
    if observed is None:
        match = re.search(r"(?:coverage|覆盖率)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%", output, re.I)
        if match:
            observed = float(match.group(1))
    if observed is None:
        raise ValueError(
            f"[GOAL_OBJECTIVE_EVIDENCE_MISSING] Verification output has no coverage result for {target}%"
        )
    if observed < target:
        raise ValueError(
            f"[GOAL_OBJECTIVE_NOT_MET] Verification reports {observed}% coverage, below {target}%"
        )
