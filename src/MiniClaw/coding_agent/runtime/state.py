from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from MiniClaw.coding_agent.memory.locking import MemoryFileLock


RunStatus = Literal[
    "running",
    "waiting_model",
    "executing_tool",
    "finalizing",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
    "paused",
]
_TERMINAL = {"completed", "failed", "cancelled", "interrupted", "paused"}


@dataclass(slots=True)
class RunState:
    version: int
    session_id: str
    run_id: str
    status: RunStatus
    started_at: str
    updated_at: str
    owner_pid: int
    turn: int = 0
    prompt: str = ""
    active_tool_call_id: str | None = None
    active_tool_name: str | None = None
    completed_tool_call_ids: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    error: str | None = None
    recovery_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: object) -> "RunState":
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError("invalid run state")
        status = value.get("status")
        if status not in {
            "running",
            "waiting_model",
            "executing_tool",
            "finalizing",
            "completed",
            "failed",
            "cancelled",
            "interrupted",
            "paused",
        }:
            raise ValueError("invalid run status")
        return cls(
            version=1,
            session_id=str(value["session_id"]),
            run_id=str(value["run_id"]),
            status=status,
            started_at=str(value["started_at"]),
            updated_at=str(value["updated_at"]),
            owner_pid=max(0, int(value.get("owner_pid", 0))),
            turn=max(0, int(value.get("turn", 0))),
            prompt=str(value.get("prompt") or ""),
            active_tool_call_id=_optional_string(value.get("active_tool_call_id")),
            active_tool_name=_optional_string(value.get("active_tool_name")),
            completed_tool_call_ids=[
                str(item) for item in value.get("completed_tool_call_ids", []) if item
            ],
            stop_reason=_optional_string(value.get("stop_reason")),
            error=_optional_string(value.get("error")),
            recovery_note=_optional_string(value.get("recovery_note")),
        )


class RunStateStore:
    """Durable latest-run state used to detect and explain interrupted work."""

    def __init__(self, session_dir: str | Path, session_id: str) -> None:
        self.path = Path(session_dir) / "run-state.json"
        self.session_id = session_id

    def read(self) -> RunState | None:
        if not self.path.exists():
            return None
        try:
            return RunState.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def recover_interrupted(self) -> RunState | None:
        with MemoryFileLock(self.path):
            state = self.read()
            if state is None or state.status in _TERMINAL:
                return state
            if _process_alive(state.owner_pid):
                return state
            previous = state.status
            state.status = "interrupted"
            state.updated_at = _now()
            state.recovery_note = (
                f"Previous process stopped while status={previous}"
                + (
                    f", tool={state.active_tool_name}/{state.active_tool_call_id}"
                    if state.active_tool_name
                    else ""
                )
            )
            state.active_tool_call_id = None
            state.active_tool_name = None
            self._persist(state)
            return state

    def begin(self, run_id: str, prompt: str) -> RunState:
        now = _now()
        state = RunState(
            version=1,
            session_id=self.session_id,
            run_id=run_id,
            status="running",
            started_at=now,
            updated_at=now,
            owner_pid=os.getpid(),
            prompt=prompt,
        )
        self._write(state)
        return state

    def transition(self, status: RunStatus, **changes: Any) -> RunState:
        with MemoryFileLock(self.path):
            state = self.read()
            if state is None:
                raise RuntimeError("run state has not been started")
            state.status = status
            state.updated_at = _now()
            for name, value in changes.items():
                if not hasattr(state, name):
                    raise ValueError(f"unknown run state field: {name}")
                setattr(state, name, value)
            self._persist(state)
            return state

    def tool_started(self, call_id: str, name: str) -> RunState:
        return self.transition(
            "executing_tool",
            active_tool_call_id=call_id,
            active_tool_name=name,
        )

    def tool_finished(self, call_id: str) -> RunState:
        state = self.read()
        completed = list(state.completed_tool_call_ids if state else ())
        if call_id not in completed:
            completed.append(call_id)
        return self.transition(
            "waiting_model",
            active_tool_call_id=None,
            active_tool_name=None,
            completed_tool_call_ids=completed,
        )

    def _write(self, state: RunState) -> None:
        with MemoryFileLock(self.path):
            self._persist(state)

    def _persist(self, state: RunState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _process_alive(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if process_id == os.getpid():
        return True
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
