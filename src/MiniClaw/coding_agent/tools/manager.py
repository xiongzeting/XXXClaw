from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from MiniClaw.cancellation import CancellationToken
from MiniClaw.llm.types import ToolInvocation

from .base import Tool, ToolResult
from .executor import Preflight, ResultTransform, ToolExecutor


@dataclass(slots=True, frozen=True)
class ToolRolePolicy:
    """Optional allow/deny filter applied after role-specific registration."""

    allow: frozenset[str] | None = None
    deny: frozenset[str] = frozenset()

    @classmethod
    def from_value(cls, value: "ToolRolePolicy | Mapping[str, Iterable[str]]") -> "ToolRolePolicy":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("tool role policy must be a mapping or ToolRolePolicy")
        unknown = set(value) - {"allow", "deny"}
        if unknown:
            raise ValueError(f"unknown tool role policy fields: {', '.join(sorted(unknown))}")
        raw_allow = value.get("allow")
        return cls(
            allow=_tool_names(raw_allow, "allow") if raw_allow is not None else None,
            deny=_tool_names(value.get("deny") or (), "deny"),
        )


class ToolManager(ToolExecutor):
    """Session-scoped tool registry with role injection and runtime filtering."""

    def __init__(
        self,
        *,
        active_role: str = "coding",
        role_policies: Mapping[str, ToolRolePolicy | Mapping[str, Iterable[str]]] | None = None,
        timeout_seconds: float | None = None,
        max_output_chars: int = 100_000,
        result_transforms: list[ResultTransform] | None = None,
        preflights: list[Preflight] | None = None,
        allowed_names: frozenset[str] | None = None,
    ) -> None:
        super().__init__(
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
            result_transforms=list(result_transforms or ()),
            preflights=list(preflights or ()),
        )
        self.active_role = _role_name(active_role, default="coding")
        self.allowed_names = allowed_names
        self.role_policies = {}
        for role, policy in (role_policies or {}).items():
            normalized_role = _role_name(role)
            self.role_policies[normalized_role] = ToolRolePolicy.from_value(policy)
        self._tool_roles: dict[str, frozenset[str] | None] = {}
        self._disabled: set[str] = set()

    def register(
        self,
        tool: Tool,
        *,
        roles: Iterable[str] | None = None,
        replace: bool = False,
    ) -> None:
        if self.allowed_names is not None and tool.name not in self.allowed_names:
            raise ValueError('Tool is outside the configured public tool set: ' + tool.name)
        if replace and tool.name in self._tools:
            self.unregister(tool.name)
        super().register(tool)
        normalized = None if roles is None else frozenset(_role_name(role) for role in roles)
        if normalized == frozenset():
            raise ValueError("role-scoped tools require at least one non-empty role")
        self._tool_roles[tool.name] = normalized

    def inject(self, role: str, tools: Iterable[Tool], *, replace: bool = False) -> None:
        normalized_role = _role_name(role)
        for tool in tools:
            self.register(tool, roles={normalized_role}, replace=replace)

    def unregister(self, name: str) -> Tool:
        try:
            tool = self._tools.pop(name)
        except KeyError as exc:
            raise KeyError(f"tool is not registered: {name}") from exc
        self._tool_roles.pop(name, None)
        self._disabled.discard(name)
        return tool

    def set_enabled(self, name: str, enabled: bool) -> None:
        if name not in self._tools:
            raise KeyError(f"tool is not registered: {name}")
        if enabled:
            self._disabled.discard(name)
        else:
            self._disabled.add(name)

    def set_role(self, role: str) -> None:
        self.active_role = _role_name(role)

    def available_names(self, role: str | None = None) -> tuple[str, ...]:
        selected_role = self.active_role if role is None else _role_name(role)
        return tuple(name for name in self._tools if self._available(name, selected_role))

    def definitions(self) -> list[dict[str, Any]]:
        names = set(self.available_names())
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            }
            for tool in self._tools.values()
            if tool.name in names
        ]

    async def execute(
        self,
        call: ToolInvocation,
        cancellation_token: CancellationToken | None = None,
    ) -> ToolResult:
        if call.name in self._tools and not self._available(call.name, self.active_role):
            return ToolResult(
                content=f"tool is not available for role '{self.active_role}': {call.name}",
                is_error=True,
                details={"status": "tool_not_available", "role": self.active_role},
            )
        return await super().execute(call, cancellation_token)

    def _available(self, name: str, role: str) -> bool:
        if name in self._disabled:
            return False
        assigned_roles = self._tool_roles.get(name)
        if assigned_roles is not None and role not in assigned_roles:
            return False
        policy = self.role_policies.get(role)
        if policy is None:
            return True
        if name in policy.deny:
            return False
        return policy.allow is None or name in policy.allow


def _role_name(value: object, *, default: str | None = None) -> str:
    if not isinstance(value, str):
        raise TypeError("role must be a string")
    normalized = value.strip()
    if normalized:
        return normalized
    if default is not None:
        return default
    raise ValueError("role must be non-empty")


def _tool_names(value: object, field: str) -> frozenset[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise TypeError(f"tool role policy '{field}' must be an iterable of tool names")
    names: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise TypeError(f"tool role policy '{field}' entries must be strings")
        name = item.strip()
        if not name:
            raise ValueError(f"tool role policy '{field}' contains an empty tool name")
        names.add(name)
    return frozenset(names)
