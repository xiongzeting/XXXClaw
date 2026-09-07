from __future__ import annotations

import re
from pathlib import Path

from MiniClaw.llm.types import ToolInvocation
from MiniClaw.coding_agent.runtime.workspace import (
    WorkspaceGuard,
    is_sensitive_relative_path,
)

from .models import RiskFinding


_TOKEN_PATTERNS = (
    (re.compile(r"\b(?:sk|xox[abprs])-[A-Za-z0-9_-]{12,}\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]+=*", re.IGNORECASE), "Bearer [REDACTED]"),
    (
        re.compile(
            r"\b([A-Z0-9_]*(?:API_KEY|TOKEN|PASSWORD|SECRET))\s*=\s*([^\s;&|]+)",
            re.IGNORECASE,
        ),
        r"\1=[REDACTED]",
    ),
)

_SHELL_RULES: tuple[tuple[str, str, str, re.Pattern[str]], ...] = (
    (
        "external-write",
        "critical",
        "命令可能向外部服务上传数据或执行外部写操作",
        re.compile(
            r"(\bcurl(?:\.exe)?\b[^\n]*(?:-x\s*(?:post|put|patch|delete)|--request\s+(?:post|put|patch|delete)|--data|-d\s|--form|-f\s)|"
            r"\bwget(?:\.exe)?\b[^\n]*--post|\bscp\b|\brsync\b[^\n]*:|"
            r"\binvoke-(?:webrequest|restmethod)\b[^\n]*-method\s+(?:post|put|patch|delete)|"
            r"\bgit\s+push\b|\bgh\s+(?:pr|issue|release|api)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "destructive-filesystem",
        "critical",
        "命令可能删除、格式化或不可逆地覆盖文件系统内容",
        re.compile(
            r"(\brm\b|\bunlink\b|\brmdir\b|\bdel(?:ete)?\b|\berase\b|\bremove-item\b|"
            r"\bclear-content\b|\bformat\b|\bmkfs\b|\bdd\s+[^\n]*\bof=|\bdiskpart\b|"
            r"\bshutil\.rmtree\b|\bos\.remove\b|\bpathlib\.[^\n]*\.unlink\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "destructive-git",
        "critical",
        "命令可能丢弃未提交工作、删除分支或强制改写仓库历史",
        re.compile(
            r"\bgit\s+(?:reset\s+--hard|clean\s+-[^\n]*f|checkout\s+(?:--|\.)|restore\b|"
            r"push\s+[^\n]*--force|branch\s+-D\b|rebase\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "database-destruction",
        "critical",
        "命令可能不可逆地删除数据库、数据表或记录",
        re.compile(r"\b(drop\s+(?:database|table)|truncate\s+table|delete\s+from)\b", re.IGNORECASE),
    ),
    (
        "sensitive-file",
        "high",
        "命令可能修改凭据、密钥或其他敏感文件",
        re.compile(
            r"(?:>|>>|\btee\b|\bset-content\b|\badd-content\b|\bcopy-item\b|\bmove-item\b)"
            r"[^\n]*(?:\.env(?:\.[\w.-]+)?|credentials?|secrets?|auth\.json|id_(?:rsa|ed25519)|\.(?:pem|key|p12|pfx)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "system-change",
        "high",
        "命令可能修改系统软件、权限、进程或服务",
        re.compile(
            r"(\bsudo\b|\bapt(?:-get)?\s+(?:install|remove|purge)|\byum\s+(?:install|remove)|"
            r"\bdnf\s+(?:install|remove)|\bapk\s+(?:add|del)|\bbrew\s+(?:install|uninstall)|"
            r"\bnpm\s+(?:install|uninstall)\s+-g|\bchmod\s+-r|\bchown\s+-r|\bkill(?:all)?\b|"
            r"\btaskkill\b|\bshutdown\b|\breboot\b|\bsystemctl\b|\bsc(?:\.exe)?\s+(?:create|delete|stop)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "network-access",
        "high",
        "命令可能联网、下载不受信任内容或读取远程资源",
        re.compile(
            r"(\bcurl(?:\.exe)?\b|\bwget(?:\.exe)?\b|\bnc\b|\bnetcat\b|\bssh\b|\bscp\b|"
            r"\bsftp\b|\brsync\b|\bgh\b|\binvoke-webrequest\b|\binvoke-restmethod\b|\biwr\b|\birm\b|"
            r"\bgit\s+(?:clone|fetch|pull|push|ls-remote|submodule)\b|"
            r"\b(?:npm|pnpm|yarn|pip|pip3)\s+(?:install|add|publish)\b|"
            r"\buv\s+(?:pip\s+install|add|publish)\b|\bcargo\s+(?:install|add|publish)\b|"
            r"\bgo\s+get\b|\bcomposer\s+(?:install|update)\b|\bbundle\s+install\b|https?://)",
            re.IGNORECASE,
        ),
    ),
    (
        "filesystem-overwrite",
        "medium",
        "命令包含直接覆盖文件内容的写法",
        re.compile(r"(?:^|[;&|]\s*|\s)(?:>|\bset-content\b|\bcopy-item\b|\bmove-item\b)", re.IGNORECASE),
    ),
)

_LEVEL_SCORE = {"medium": 1, "high": 2, "critical": 3}


def classify_tool_risks(
    call: ToolInvocation,
    workspace: str | Path | WorkspaceGuard,
) -> tuple[RiskFinding, ...]:
    if call.name == "bash":
        command = str(call.arguments.get("command", ""))
        matches = [rule for rule in _SHELL_RULES if rule[3].search(command)]
        findings: list[RiskFinding] = []
        seen: set[str] = set()
        for risk, level, reason, _ in matches:
            if risk in seen:
                continue
            seen.add(risk)
            findings.append(
                RiskFinding(risk, level, reason, sanitize_preview(command))  # type: ignore[arg-type]
            )
        return tuple(findings)

    if call.name in {"write", "edit"}:
        raw_path = str(call.arguments.get("path", ""))
        normalized_path = _normalized_workspace_path(workspace, raw_path)
        if _is_approval_policy_path(normalized_path):
            return (
                RiskFinding(
                    "approval-policy-change",
                    "critical",
                    f"操作将修改项目审批策略：{raw_path}",
                    _file_preview(call),
                ),
            )
        if normalized_path is not None and is_sensitive_relative_path(normalized_path):
            return (
                RiskFinding(
                    "sensitive-file",
                    "high",
                    f"操作将修改凭据、密钥或其他敏感文件：{raw_path}",
                    _file_preview(call),
                ),
            )
        if call.name == "write" and _existing_workspace_file(workspace, raw_path):
            return (
                RiskFinding(
                    "file-overwrite",
                    "medium",
                    f"write 将完整替换已有文件：{raw_path}",
                    _file_preview(call),
                ),
            )
        return ()

    return ()


def classify_tool_risk(
    call: ToolInvocation,
    workspace: str | Path | WorkspaceGuard,
) -> RiskFinding | None:
    """Compatibility helper returning the highest-level finding."""

    findings = classify_tool_risks(call, workspace)
    if not findings:
        return None
    return max(findings, key=lambda finding: _LEVEL_SCORE[finding.level])


def sanitize_preview(value: str, max_chars: int = 2_000) -> str:
    sanitized = value
    for pattern, replacement in _TOKEN_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    if len(sanitized) > max_chars:
        return f"{sanitized[:max_chars]}…[preview truncated]"
    return sanitized


def _file_preview(call: ToolInvocation) -> str:
    path = str(call.arguments.get("path", ""))
    if call.name == "write":
        content = sanitize_preview(str(call.arguments.get("content", "")), 800)
        return f"path: {path}\ncontent preview:\n{content}"
    edits = call.arguments.get("edits")
    count = len(edits) if isinstance(edits, list) else 0
    return f"path: {path}\nedit blocks: {count}"


def _is_approval_policy_path(value: str | None) -> bool:
    return bool(value and value.casefold() == ".miniclaw/approval.json")


def _normalized_workspace_path(
    workspace: str | Path | WorkspaceGuard,
    value: str,
) -> str | None:
    boundary = workspace if isinstance(workspace, WorkspaceGuard) else WorkspaceGuard(workspace)
    try:
        return boundary.relative_path(boundary.normalize(value, must_exist=False))
    except (OSError, PermissionError, ValueError):
        return None


def _existing_workspace_file(
    workspace: str | Path | WorkspaceGuard,
    value: str,
) -> bool:
    boundary = workspace if isinstance(workspace, WorkspaceGuard) else WorkspaceGuard(workspace)
    try:
        candidate = boundary.normalize(value, must_exist=False)
    except (OSError, ValueError):
        return False
    return candidate.is_file()
