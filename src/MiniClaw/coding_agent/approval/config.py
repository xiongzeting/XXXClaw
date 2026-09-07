from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ApprovalPolicy


@dataclass(slots=True, frozen=True)
class ApprovalAllowRule:
    tool: str
    risk: str | None = None
    command_glob: str | None = None
    path_glob: str | None = None


@dataclass(slots=True, frozen=True)
class ApprovalSettings:
    policy: ApprovalPolicy = "ask"
    timeout_seconds: float = 300.0
    allowlist: tuple[ApprovalAllowRule, ...] = ()
    config_path: Path | None = None


def load_approval_settings(
    workspace: str | Path,
    environment: Mapping[str, str] | None = None,
    *,
    policy: str | None = None,
    timeout_seconds: float | None = None,
) -> ApprovalSettings:
    env = os.environ if environment is None else environment
    root = Path(workspace).resolve()
    configured_path = _first(env, "MINICLAW_APPROVAL_CONFIG")
    config_path = (
        Path(configured_path).expanduser()
        if configured_path
        else root / ".miniclaw" / "approval.json"
    )
    if not config_path.is_absolute():
        config_path = (root / config_path).resolve()
    payload: dict[str, Any] = {}
    if config_path.is_file():
        loaded = json.loads(config_path.read_text(encoding="utf-8-sig", errors="strict"))
        if not isinstance(loaded, dict):
            raise ValueError(f"Approval config must be a JSON object: {config_path}")
        payload = loaded

    policy_value = (
        policy
        or _first(
            env,
            "MINICLAW_APPROVAL_POLICY",
        )
        or payload.get("policy")
        or "ask"
    )
    if not isinstance(policy_value, str) or policy_value.lower() not in {"allow", "ask", "deny"}:
        raise ValueError("Approval policy must be allow, ask, or deny")

    raw_timeout: Any
    if timeout_seconds is not None:
        raw_timeout = timeout_seconds
    elif _first(env, "MINICLAW_APPROVAL_TIMEOUT_SECONDS") is not None:
        raw_timeout = _first(env, "MINICLAW_APPROVAL_TIMEOUT_SECONDS")
    else:
        raw_timeout = payload.get("timeout_seconds") or 300
    try:
        parsed_timeout = float(raw_timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("Approval timeout must be a positive number") from exc
    if parsed_timeout <= 0:
        raise ValueError("Approval timeout must be a positive number")

    raw_allowlist = payload.get("allowlist", [])
    if not isinstance(raw_allowlist, list):
        raise ValueError("Approval config allowlist must be an array")
    allowlist = tuple(_parse_rule(item, index) for index, item in enumerate(raw_allowlist))
    return ApprovalSettings(
        policy=policy_value.lower(),  # type: ignore[arg-type]
        timeout_seconds=parsed_timeout,
        allowlist=allowlist,
        config_path=config_path if config_path.is_file() else None,
    )


def _parse_rule(value: Any, index: int) -> ApprovalAllowRule:
    if not isinstance(value, dict):
        raise ValueError(f"Approval allowlist item {index + 1} must be an object")
    allowed_keys = {"tool", "risk", "command_glob", "path_glob"}
    extras = set(value) - allowed_keys
    if extras:
        raise ValueError(
            f"Approval allowlist item {index + 1} has unknown fields: {', '.join(sorted(extras))}"
        )
    tool = value.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        raise ValueError(f"Approval allowlist item {index + 1} requires tool")
    fields: dict[str, str | None] = {}
    for name in ("risk", "command_glob", "path_glob"):
        item = value.get(name)
        if item is not None and (not isinstance(item, str) or not item.strip()):
            raise ValueError(f"Approval allowlist item {index + 1} field {name} must be a string")
        fields[name] = item.strip() if isinstance(item, str) else None
    if fields["command_glob"] is None and fields["path_glob"] is None and fields["risk"] is None:
        raise ValueError(
            f"Approval allowlist item {index + 1} must restrict risk, command_glob, or path_glob"
        )
    return ApprovalAllowRule(tool=tool.strip(), **fields)


def _first(environment: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = environment.get(name)
        if value and value.strip():
            return value.strip()
    return None
