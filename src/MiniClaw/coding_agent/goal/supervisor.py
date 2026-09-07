from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from MiniClaw.agent.events import AgentEvent
from MiniClaw.llm.types import ChatMessage

from .prompts import (
    assess_goal_continuation,
    build_goal_continuation_prompt,
    build_goal_initial_prompt,
)
from .store import GoalStore
from .state import GoalState


RunOnce = Callable[[str], AsyncIterator[AgentEvent]]
AttemptFinishedObserver = Callable[[GoalState, str | None], None]


class GoalSupervisor:
    """Outer loop: a natural model stop ends one attempt, not the persisted Goal."""

    def __init__(
        self,
        store: GoalStore,
        run_once: RunOnce,
        attempt_finished_observer: AttemptFinishedObserver | None = None,
    ) -> None:
        self.store = store
        self.run_once = run_once
        self.attempt_finished_observer = attempt_finished_observer

    async def run(self, initial_prompt: str | None = None) -> AsyncIterator[AgentEvent]:
        first_attempt = True
        while True:
            current = self.store.read()
            if current is None or current.status not in {"active", "verifying"}:
                return
            decision = self.store.begin_attempt()
            if not decision.allowed:
                message = ChatMessage(
                    role="assistant",
                    content=decision.reason or f"Goal status is {decision.state.status}",
                )
                yield AgentEvent(
                    type="run_finished",
                    message=message,
                    details={"stop_reason": "stop", "goal_status": decision.state.status},
                )
                return
            if first_attempt and initial_prompt is not None:
                prompt = build_goal_initial_prompt(decision.state, initial_prompt)
            else:
                prompt = build_goal_continuation_prompt(decision.state, self.store)
            first_attempt = False
            final_text = ""
            run_error = False
            stop_reason = "stop"
            cost_usd = 0.0
            trace_run_id: str | None = None
            try:
                async for event in self.run_once(prompt):
                    if event.type == "run_finished":
                        if event.message and event.message.content.strip():
                            final_text = event.message.content.strip()
                        run_error = event.is_error
                        if event.details and isinstance(event.details.get("stop_reason"), str):
                            stop_reason = event.details["stop_reason"]
                        if event.details and isinstance(event.details.get("cost_usd"), (int, float)):
                            cost_usd = float(event.details["cost_usd"])
                        if event.details and isinstance(event.details.get("run_id"), str):
                            trace_run_id = event.details["run_id"]
                    yield event
            except Exception as exc:
                run_error = True
                stop_reason = "error"
                final_text = f"{type(exc).__name__}: {exc}"
                yield AgentEvent(type="error", text=final_text, is_error=True)
                yield AgentEvent(
                    type="run_finished",
                    is_error=True,
                    details={"stop_reason": "error", "error": final_text},
                )
            updated = self.store.finish_attempt(cost_usd, final_text)
            if self.attempt_finished_observer:
                try:
                    self.attempt_finished_observer(updated, trace_run_id)
                except Exception:
                    pass
            if run_error or stop_reason in {"error", "aborted", "budget_exhausted"}:
                return
            if updated.status not in {"active", "verifying"}:
                return
            if assess_goal_continuation(updated, self.store) is None:
                return
