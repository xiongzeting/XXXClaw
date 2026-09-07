from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any

from MiniClaw.cancellation import CancellationToken, OperationCancelledError
from MiniClaw.coding_agent.tools.base import ToolResult

from .judge import GoalJudge
from .prompts import format_goal_status
from .state import GoalCriterionEvidence
from .store import GoalStore


@dataclass(slots=True)
class GoalTool:
    store: GoalStore

    name = "goal"
    description = (
        "Inspect or update the persisted long-running goal. Use checkpoint for material progress, "
        "waiting_for_user only when a user decision is unavoidable, and failed only for a genuine terminal failure."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "checkpoint", "waiting_for_user", "failed"],
            },
            "summary": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    async def execute(
        self,
        arguments: dict[str, Any],
        cancellation_token: CancellationToken | None = None,
    ) -> ToolResult:
        action = arguments["action"]
        if action == "status":
            state = self.store.read()
            return ToolResult(
                content=format_goal_status(state),
                details={"status": state.status if state else "none", "attempt_count": state.attempt_count if state else 0},
            )
        if self.store.read() is None:
            return ToolResult(
                content="No active Goal exists. No persistent Goal state was changed.",
                details={"status": "none", "attempt_count": 0},
            )
        summary = str(arguments.get("summary") or "").strip()
        if not summary:
            raise ValueError("[GOAL_SUMMARY_REQUIRED] summary is required")
        if action == "checkpoint":
            state = self.store.checkpoint(summary)
        elif action == "waiting_for_user":
            state = self.store.wait_for_user(summary)
        else:
            state = self.store.fail(summary)
        return ToolResult(
            content=f"Goal status: {state.status}\n{summary}",
            details={"status": state.status, "attempt_count": state.attempt_count},
        )


@dataclass(slots=True)
class GoalCompleteTool:
    store: GoalStore
    judge: GoalJudge | None = None

    name = "goal_complete"
    description = "Submit the final answer for the active goal and stop execution. Saved for later review; this does not certify correctness."
    input_schema = {'type':'object','properties':{'final_result':{'type':'string'}},
                    'required':['final_result'],'additionalProperties':False}

    async def execute(self, arguments, cancellation_token=None):
        state = self.store.submit_completion(arguments['final_result'])
        return ToolResult(content='Final answer submitted for later review: ' + state.final_result,
                          details={'status':state.status,'judge_status':'pending','completion_basis':'model_submission'})
