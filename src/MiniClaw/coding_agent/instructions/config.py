from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


DEFAULT_INSTRUCTION_TOKEN_BUDGET = 12_000
DEFAULT_FILENAMES = ("AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD")


@dataclass(slots=True, frozen=True)
class InstructionConfig:
    enabled: bool = True
    token_budget: int = DEFAULT_INSTRUCTION_TOKEN_BUDGET
    global_directories: tuple[Path, ...] = ()
    filenames: tuple[str, ...] = DEFAULT_FILENAMES


def load_instruction_config(
    environment: Mapping[str, str] | None = None,
    *,
    home: str | Path | None = None,
) -> InstructionConfig:
    env = os.environ if environment is None else environment
    enabled = _boolean(env.get("MINICLAW_INSTRUCTIONS_ENABLED"), True)
    budget = _positive_int(
        env.get("MINICLAW_INSTRUCTIONS_TOKEN_BUDGET"),
        DEFAULT_INSTRUCTION_TOKEN_BUDGET,
        "MINICLAW_INSTRUCTIONS_TOKEN_BUDGET",
    )
    configured = env.get("MINICLAW_AGENT_DIRS") or env.get("MINICLAW_AGENT_DIR")
    if configured:
        directories = tuple(
            Path(value).expanduser().resolve(strict=False)
            for value in configured.split(os.pathsep)
            if value.strip()
        )
    else:
        user_home = Path(home).expanduser() if home is not None else Path.home()
        directories = ((user_home / ".miniclaw").resolve(strict=False),)
    return InstructionConfig(
        enabled=enabled,
        token_budget=budget,
        global_directories=directories,
    )


def _boolean(raw: str | None, fallback: bool) -> bool:
    if raw is None or not raw.strip():
        return fallback
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("MINICLAW_INSTRUCTIONS_ENABLED must be true or false")


def _positive_int(raw: str | None, fallback: int, name: str) -> int:
    if raw is None or not raw.strip():
        return fallback
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value
