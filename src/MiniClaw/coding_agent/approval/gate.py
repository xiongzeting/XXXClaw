from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import inspect
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from MiniClaw.llm.types import ToolInvocation
from MiniClaw.coding_agent.runtime.workspace import WorkspaceGuard
from MiniClaw.coding_agent.tools.base import ToolResult

from .config import ApprovalAllowRule, ApprovalSettings
from .models import ApprovalDecision, ApprovalRequest, RiskFinding
from .risk import classify_tool_risks


ApprovalHandler = Callable[[ApprovalRequest], bool | Awaitable[bool]]
ApprovalRecorder = Callable[[str, ApprovalRequest, ApprovalDecision | None], None]


@dataclass(slots=True)
class ApprovalGate:
    workspace: Path | WorkspaceGuard
    settings: ApprovalSettings
    handler: ApprovalHandler | None = None
    recorder: ApprovalRecorder | None = None
    boundary: WorkspaceGuard = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.boundary = (
            self.workspace
            if isinstance(self.workspace, WorkspaceGuard)
            else WorkspaceGuard(self.workspace)
        )

    async def authorize(self, call: ToolInvocation) -> ToolResult | None:
        findings = classify_tool_risks(call, self.boundary)
        if not findings:
            return None
        finding = _highest_finding(findings)
        capabilities = tuple(item.risk for item in findings)
        call_hash = _normalized_call_hash(call, self.boundary)
        request = ApprovalRequest(
            approval_id=uuid.uuid4().hex[:6].upper(),
            tool_call_id=call.call_id,
            tool_name=call.name,
            risk=finding.risk,
            level=finding.level,
            reason="；".join(item.reason for item in findings),
            preview=finding.preview,
            capabilities=capabilities,
            normalized_call_hash=call_hash,
        )
        self._record("requested", request, None)

        if _all_capabilities_allowed(
            self.settings.allowlist,
            call,
            findings,
            self.boundary,
        ):
            decision: ApprovalDecision = "allowed-by-whitelist"
        elif self.settings.policy == "allow":
            decision = "allowed-by-policy"
        elif self.settings.policy == "deny":
            decision = "denied-by-policy"
        elif self.handler is None:
            decision = "unavailable"
        else:
            try:
                response = self.handler(request)
                approved = await asyncio.wait_for(
                    response if inspect.isawaitable(response) else _immediate(response),
                    timeout=self.settings.timeout_seconds,
                )
                decision = "approved" if approved else "denied"
            except asyncio.TimeoutError:
                decision = "timeout"
            except Exception:
                decision = "unavailable"

        if decision in {"allowed-by-policy", "allowed-by-whitelist", "approved"}:
            if _normalized_call_hash(call, self.boundary) != request.normalized_call_hash:
                decision = "call-mismatch"

        self._record("decision", request, decision)
        if decision in {"allowed-by-policy", "allowed-by-whitelist", "approved"}:
            return None
        code = {
            "denied-by-policy": "APPROVAL_REQUIRED",
            "denied": "APPROVAL_DENIED",
            "timeout": "APPROVAL_TIMEOUT",
            "unavailable": "APPROVAL_UNAVAILABLE",
            "call-mismatch": "APPROVAL_CALL_CHANGED",
        }[decision]
        message = {
            "denied-by-policy": "危险操作已被项目策略拒绝",
            "denied": "用户拒绝了危险操作",
            "timeout": f"审批等待超过 {self.settings.timeout_seconds:g} 秒，已自动拒绝",
            "unavailable": "当前入口没有可用的审批交互",
            "call-mismatch": "审批后的规范化 Tool Call 已发生变化，已拒绝执行",
        }[decision]
        return ToolResult(
            content=f"[{code}] {message}：{request.reason}",
            is_error=True,
            details={"approval": {**asdict(request), "decision": decision}},
        )

    def _record(
        self,
        phase: str,
        request: ApprovalRequest,
        decision: ApprovalDecision | None,
    ) -> None:
        if self.recorder is not None:
            self.recorder(phase, request, decision)


async def _immediate(value: bool) -> bool:
    return value


def _all_capabilities_allowed(
    rules: tuple[ApprovalAllowRule, ...],
    call: ToolInvocation,
    findings: tuple[RiskFinding, ...],
    boundary: WorkspaceGuard,
) -> bool:
    return bool(rules) and all(
        any(_matches(rule, call, finding.risk, boundary) for rule in rules)
        for finding in findings
    )


def _matches(
    rule: ApprovalAllowRule,
    call: ToolInvocation,
    risk: str,
    boundary: WorkspaceGuard,
) -> bool:
    if not fnmatch.fnmatchcase(call.name, rule.tool):
        return False
    if rule.risk is not None and not fnmatch.fnmatchcase(risk, rule.risk):
        return False
    if rule.command_glob is not None:
        command = call.arguments.get("command")
        if not isinstance(command, str) or not fnmatch.fnmatchcase(command, rule.command_glob):
            return False
        if _shell_structure(command) != _shell_structure(rule.command_glob):
            return False
    if rule.path_glob is not None:
        raw_path = call.arguments.get("path")
        if not isinstance(raw_path, str) or _contains_parent_reference(raw_path):
            return False
        try:
            path = boundary.normalize(raw_path, must_exist=False)
            normalized = boundary.relative_path(path)
        except (OSError, PermissionError, ValueError):
            return False
        if not fnmatch.fnmatchcase(normalized, rule.path_glob.replace("\\", "/")):
            return False
    return True


def _highest_finding(findings: tuple[RiskFinding, ...]) -> RiskFinding:
    scores = {"medium": 1, "high": 2, "critical": 3}
    return max(findings, key=lambda finding: scores[finding.level])


def _contains_parent_reference(value: str) -> bool:
    return ".." in value.replace("\\", "/").split("/")


def _shell_structure(value: str) -> tuple[str, ...]:
    return tuple(
        re.findall(r"(?:&&|\|\||>>|<<|[;&|<>]|\r\n|\r|\n|`|\$\()", value)
    )


def _normalized_call_hash(call: ToolInvocation, boundary: WorkspaceGuard) -> str:
    arguments: dict[str, Any] = dict(call.arguments)
    raw_path = arguments.get("path")
    if isinstance(raw_path, str):
        try:
            arguments["path"] = boundary.relative_path(
                boundary.normalize(raw_path, must_exist=False)
            )
        except (OSError, PermissionError, ValueError):
            arguments["path"] = raw_path.replace("\\", "/")
    payload = {
        "call_id": call.call_id,
        "name": call.name,
        "arguments": arguments,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
