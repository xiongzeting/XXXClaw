from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ApprovalPolicy = Literal["allow", "ask", "deny"]
ApprovalDecision = Literal[
    "allowed-by-policy",
    "allowed-by-whitelist",
    "denied-by-policy",
    "approved",
    "denied",
    "timeout",
    "unavailable",
    "call-mismatch",
]
RiskLevel = Literal["medium", "high", "critical"]


@dataclass(slots=True, frozen=True)
class RiskFinding:
    risk: str
    level: RiskLevel
    reason: str
    preview: str


@dataclass(slots=True, frozen=True)
class ApprovalRequest:
    approval_id: str
    tool_call_id: str
    tool_name: str
    risk: str
    level: RiskLevel
    reason: str
    preview: str
    capabilities: tuple[str, ...]
    normalized_call_hash: str
