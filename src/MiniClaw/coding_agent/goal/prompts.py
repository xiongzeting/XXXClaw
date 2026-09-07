from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from .state import GoalState
from .store import GoalStore


@dataclass(slots=True, frozen=True)
class GoalCommand:
    action: Literal["start", "status", "resume", "cancel"]
    goal: str | None = None
    acceptance_criteria: list[str] | None = None
    reason: str | None = None


def parse_goal_command(text: str) -> GoalCommand | None:
    lines = text.replace("\r\n", "\n").split("\n")
    first = lines[0].strip() if lines else ""
    match = re.match(r"^(?:/goal|goal|目标)(?:\s+(.*))?$", first, re.I)
    if not match:
        return None
    argument = (match.group(1) or "").strip()
    normalized = argument.lower()
    if not argument or normalized in {"status", "状态"}:
        return GoalCommand("status")
    if normalized in {"resume", "继续"}:
        return GoalCommand("resume")
    if normalized in {"cancel", "取消"}:
        return GoalCommand("cancel")
    if normalized.startswith("cancel "):
        return GoalCommand("cancel", reason=argument[7:].strip())
    if argument.startswith("取消 "):
        return GoalCommand("cancel", reason=argument[3:].strip())

    body = [re.sub(r"^new\s+", "", argument, flags=re.I), *lines[1:]]
    header = next(
        (
            index
            for index, line in enumerate(body)
            if re.match(
                r"^(?:acceptance(?:_criteria| criteria)?|验收条件)\s*[:：]\s*$",
                line.strip(),
                re.I,
            )
        ),
        -1,
    )
    goal_lines = body if header == -1 else body[:header]
    goal = " ".join(line.strip() for line in goal_lines if line.strip())
    if not goal:
        raise ValueError("[GOAL_REQUIRED] Usage: /goal <goal description>")
    criteria_lines = [] if header == -1 else body[header + 1 :]
    criteria = [
        re.sub(r"^(?:[-*]\s+|\d+[.)]\s+)", "", line.strip()).strip()
        for line in criteria_lines
        if line.strip()
    ]
    return GoalCommand("start", goal=goal, acceptance_criteria=criteria or [goal])


def format_goal_status(state: GoalState | None) -> str:
    if state is None:
        return "当前会话还没有 Goal。"
    criteria = "\n".join(
        f"{index}. {criterion}" for index, criterion in enumerate(state.acceptance_criteria, 1)
    )
    verification = "none"
    if state.last_verification:
        outcome = (
            "passed"
            if state.last_verification.passed is True
            else "failed"
            if state.last_verification.passed is False
            else "running"
        )
        verification = f"{outcome}: {state.last_verification.command}"
    return "\n".join(
        [
            f"Goal: {state.goal}",
            f"Status: {state.status}",
            f"Attempts: {state.attempt_count}/{state.limits.max_attempts}",
            f"Cost: ${state.spent_cost_usd:.4f} / ${state.limits.max_cost_usd:.2f}",
            f"Created: {state.created_at}",
            f"Last checkpoint: {state.last_checkpoint.summary if state.last_checkpoint else 'none'}",
            f"Last judge rejection: {state.last_judge_rejection.summary if state.last_judge_rejection else 'none'}",
            f"Last verification: {verification}",
            f"Acceptance criteria:\n{criteria}",
            f"Final result: {state.final_result or 'none'}",
        ]
    )


def build_goal_prompt(state: GoalState | None) -> str:
    if state is None:
        return "No active long-running goal."
    return json.dumps(state.to_dict(), ensure_ascii=False, indent=2)


def assess_goal_continuation(state: GoalState, store: GoalStore) -> tuple[str, list[str]] | None:
    if state.status not in {"active", "verifying"}:
        return None
    return "work", ["the goal has not been submitted"]


def build_goal_initial_prompt(state: GoalState, user_prompt: str) -> str:
    criteria = "\n".join(
        f"{index}. {criterion}" for index, criterion in enumerate(state.acceptance_criteria, 1)
    )
    return "\n".join(
        [
            user_prompt,
            "",
            "Goal execution contract:",
            f"- Persisted goal: {state.goal}",
            f"- Exact acceptance criteria:\n{criteria}",
            "- Work through every criterion. A normal assistant stop is only one attempt, not completion.",
            "- Translate each criterion into executable checks, including relevant boundary and failure paths.",
            "- After the final file modification, run appropriate normal checks when needed.",
            "- Submit the final answer with goal_complete(final_result=...) when done; independent judging happens later.",
            "- If a user decision is unavoidable, call goal(action=waiting_for_user) and state the exact decision.",
        ]
    )


def build_goal_continuation_prompt(state: GoalState, store: GoalStore) -> str:
    assessment = assess_goal_continuation(state, store)
    if assessment is None:
        return f"Goal status is {state.status}; do not continue autonomous work."
    mode, reasons = assessment
    criteria = "\n".join(
        f"{index}. {criterion}" for index, criterion in enumerate(state.acceptance_criteria, 1)
    )

    return "\n".join(
        [
            "Continue working autonomously on the persisted Goal.",
            f"Goal: {state.goal}",
            f"Exact acceptance criteria:\n{criteria}",
            f"Risk reasons: {'; '.join(reasons)}.",
            "Do not stop merely to report partial progress.",
            "When the requested work is done, submit your final answer with goal_complete(final_result=...).",
            "Do not invent a verification submission format.",
            "If a user decision is unavoidable, call goal(action=waiting_for_user).",
        ]
    )
