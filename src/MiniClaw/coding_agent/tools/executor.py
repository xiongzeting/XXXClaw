from __future__ import annotations

import asyncio
import copy
import contextlib
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from MiniClaw.cancellation import CancellationToken, ToolCancelledError
from MiniClaw.llm.types import ToolInvocation

from .base import Tool, ToolResult


ResultTransform = Callable[[ToolInvocation, ToolResult], ToolResult | Awaitable[ToolResult]]
Preflight = Callable[[ToolInvocation], ToolResult | None | Awaitable[ToolResult | None]]


@dataclass(slots=True)
class ToolExecutor:
    timeout_seconds: float | None = None
    max_output_chars: int = 100_000
    result_transforms: list[ResultTransform] = field(default_factory=list)
    preflights: list[Preflight] = field(default_factory=list)
    _tools: dict[str, Tool] = field(default_factory=dict, init=False, repr=False)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    async def execute(
        self,
        call: ToolInvocation,
        cancellation_token: CancellationToken | None = None,
    ) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(content=f"unknown tool: {call.name}", is_error=True)
        started = False
        try:
            if cancellation_token is not None:
                cancellation_token.raise_if_tool_cancelled(stage="tool_lookup")
            arguments = copy.deepcopy(call.arguments)
            prepare = getattr(tool, "prepare_arguments", None)
            if prepare is not None:
                arguments = prepare(arguments)
            self._validate(arguments, tool.input_schema)
            prepared_call = ToolInvocation(call.call_id, call.name, arguments)
            result: ToolResult | None = None
            for preflight in self.preflights:
                if cancellation_token is not None:
                    cancellation_token.raise_if_tool_cancelled(stage="tool_preflight")
                checked = preflight(prepared_call)
                blocked = (
                    await _await_cancelable(
                        checked,
                        cancellation_token,
                        timeout=None,
                        stage="tool_preflight",
                    )
                    if inspect.isawaitable(checked)
                    else checked
                )
                if blocked is not None:
                    result = blocked
                    break
            if result is None:
                if cancellation_token is not None:
                    cancellation_token.raise_if_tool_cancelled(stage="tool_execution")
                started = True
                execution = _invoke_tool(tool, arguments, cancellation_token)
                result = await _await_cancelable(
                    execution,
                    cancellation_token,
                    timeout=self.timeout_seconds,
                    stage="tool_execution",
                )
        except ToolCancelledError:
            raise
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            if self.timeout_seconds is None:
                result = ToolResult(content=f"TimeoutError: {exc}", is_error=True)
            else:
                result = ToolResult(
                    content=f"tool timed out after {self.timeout_seconds:g} seconds",
                    is_error=True,
                )
        except Exception as exc:
            result = ToolResult(content=f"{type(exc).__name__}: {exc}", is_error=True)

        if not started:
            result.details.setdefault('not_started', True)
        for transform in self.result_transforms:
            try:
                transformed = transform(call, result)
                result = await transformed if inspect.isawaitable(transformed) else transformed
            except Exception as exc:
                result.details = {
                    **result.details,
                    "result_transform_error": f"{type(exc).__name__}: {exc}",
                }

        if len(result.content) > self.max_output_chars:
            omitted = len(result.content) - self.max_output_chars
            result.content = f"{result.content[:self.max_output_chars]}\n[truncated {omitted} characters]"
            result.details = {**result.details, "truncated": True, "omitted_chars": omitted}
        return result

    @staticmethod
    def _validate(arguments: dict[str, Any], schema: dict[str, Any]) -> None:
        ToolExecutor._validate_value(arguments, schema, "arguments")

    @staticmethod
    def _validate_value(value: Any, schema: dict[str, Any], location: str) -> None:
        expected_name = schema.get("type")
        valid = True
        if expected_name == "string":
            valid = isinstance(value, str)
        elif expected_name == "integer":
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif expected_name == "number":
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif expected_name == "boolean":
            valid = isinstance(value, bool)
        elif expected_name == "object":
            valid = isinstance(value, dict)
        elif expected_name == "array":
            valid = isinstance(value, list)
        if not valid:
            raise TypeError(f"{location} must be {expected_name}")

        allowed = schema.get("enum")
        if allowed is not None and value not in allowed:
            raise ValueError(f"{location} must be one of: {', '.join(map(str, allowed))}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = schema.get("minimum")
            if minimum is not None and value < minimum:
                raise ValueError(f"{location} must be at least {minimum}")
            maximum = schema.get("maximum")
            if maximum is not None and value > maximum:
                raise ValueError(f"{location} must be at most {maximum}")

        if isinstance(value, str):
            minimum_length = schema.get("minLength")
            if minimum_length is not None and len(value) < minimum_length:
                raise ValueError(
                    f"{location} must contain at least {minimum_length} character(s)"
                )
            maximum_length = schema.get("maxLength")
            if maximum_length is not None and len(value) > maximum_length:
                raise ValueError(
                    f"{location} must contain at most {maximum_length} character(s)"
                )

        if isinstance(value, list):
            minimum_items = schema.get("minItems")
            if minimum_items is not None and len(value) < minimum_items:
                raise ValueError(f"{location} must contain at least {minimum_items} item(s)")
            item_schema = schema.get("items")
            if item_schema:
                for index, item in enumerate(value):
                    ToolExecutor._validate_value(item, item_schema, f"{location}[{index}]")

        if isinstance(value, dict):
            required = schema.get("required") or []
            missing = [name for name in required if name not in value]
            if missing:
                raise ValueError(f"missing required arguments at {location}: {', '.join(missing)}")
            properties = schema.get("properties") or {}
            if schema.get("additionalProperties") is False:
                extras = [name for name in value if name not in properties]
                if extras:
                    raise ValueError(f"unexpected arguments at {location}: {', '.join(extras)}")
            for name, item in value.items():
                child_schema = properties.get(name)
                if child_schema:
                    ToolExecutor._validate_value(item, child_schema, f"{location}.{name}")


def _invoke_tool(
    tool: Tool,
    arguments: dict[str, Any],
    cancellation_token: CancellationToken | None,
) -> Awaitable[ToolResult]:
    try:
        parameters = inspect.signature(tool.execute).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "cancellation_token" in parameters:
        return tool.execute(arguments, cancellation_token=cancellation_token)  # type: ignore[call-arg]
    return tool.execute(arguments)


async def _await_cancelable(
    awaitable: Awaitable[Any],
    cancellation_token: CancellationToken | None,
    *,
    timeout: float | None,
    stage: str,
) -> Any:
    operation = asyncio.ensure_future(awaitable)
    if cancellation_token is not None:
        try:
            cancellation_token.raise_if_tool_cancelled(stage=stage)
        except BaseException:
            operation.cancel()
            with contextlib.suppress(BaseException):
                await operation
            raise
    cancellation = (
        asyncio.create_task(cancellation_token.wait())
        if cancellation_token is not None
        else None
    )
    waiters = {operation}
    if cancellation is not None:
        waiters.add(cancellation)
    try:
        done, _ = await asyncio.wait(
            waiters,
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if operation in done:
            return operation.result()
        if cancellation is not None and cancellation in done:
            operation.cancel()
            with contextlib.suppress(BaseException):
                await operation
            raise ToolCancelledError(cancellation_token.reason, stage=stage)
        operation.cancel()
        with contextlib.suppress(BaseException):
            await operation
        raise asyncio.TimeoutError
    except asyncio.CancelledError:
        operation.cancel()
        with contextlib.suppress(BaseException):
            await operation
        raise
    finally:
        if cancellation is not None:
            cancellation.cancel()
            with contextlib.suppress(BaseException):
                await cancellation
