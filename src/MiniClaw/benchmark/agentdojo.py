from __future__ import annotations

"""Run AgentDojo task suites through MiniClaw's model and agent loop.

AgentDojo remains the owner of the virtual environment, attacks, and scorers.
This module only adapts its function runtime to MiniClaw's provider-neutral
tool protocol so benchmark results measure the MiniClaw execution path instead
of AgentDojo's built-in OpenAI loop.
"""

import argparse
import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from MiniClaw.agent.loop import AgentLoop
from MiniClaw.cancellation import CancellationToken
from MiniClaw.coding_agent.tools.base import ToolResult
from MiniClaw.coding_agent.tools.executor import ToolExecutor
from MiniClaw.llm.config import load_llm_settings
from MiniClaw.llm.env_file import merged_environment, read_env_file
from MiniClaw.llm.factory import create_model_client, model_profile_from_settings
from MiniClaw.llm.types import ModelProfile


def _require_agentdojo() -> None:
    try:
        import agentdojo  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "AgentDojo is not installed in this Python environment. Install the "
            "downloaded repository into an isolated environment before running this benchmark."
        ) from exc


def _text_block(content: str) -> dict[str, str]:
    return {"type": "text", "content": content}


@dataclass(slots=True)
class QueryMetrics:
    query_id: str
    duration_seconds: float
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    model_errors: tuple[str, ...]


class AgentDojoFunctionTool:
    """Expose one AgentDojo function through MiniClaw's Tool protocol."""

    def __init__(self, runtime: Any, environment: Any, function: Any) -> None:
        self.runtime = runtime
        self.environment = environment
        self.function = function
        self.name = str(function.name)
        self.description = str(function.description)
        self.input_schema = function.parameters.model_json_schema()

    async def execute(
        self,
        arguments: dict[str, Any],
        cancellation_token: CancellationToken | None = None,
    ) -> ToolResult:
        if cancellation_token is not None:
            cancellation_token.raise_if_tool_cancelled(stage="agentdojo_tool")
        from agentdojo.agent_pipeline.tool_execution import tool_result_to_str

        result, error = self.runtime.run_function(
            self.environment,
            self.name,
            arguments,
        )
        return ToolResult(
            content=tool_result_to_str(result),
            is_error=error is not None,
            details={"agentdojo_error": error} if error is not None else {},
        )


class MiniClawAgentDojoPipeline:
    """Synchronous AgentDojo pipeline facade backed by MiniClaw AgentLoop."""

    def __init__(
        self,
        model_client: Any,
        profile: ModelProfile,
        system_prompt: str,
        *,
        max_turns: int = 15,
        tool_timeout_seconds: float = 60.0,
        max_tool_output_chars: int = 100_000,
    ) -> None:
        self.model_client = model_client
        self.profile = profile
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.tool_timeout_seconds = tool_timeout_seconds
        self.max_tool_output_chars = max_tool_output_chars
        # AgentDojo attack templates infer a prose model identity by matching a
        # registered provider/model token in the pipeline name. Keep the real
        # model id for auditability while marking arbitrary compatible models
        # with AgentDojo's generic openai-compatible identity.
        self.name = f"miniclaw-openai-compatible-{profile.model_id}"
        self.query_metrics: list[QueryMetrics] = []

    def query(
        self,
        query: str,
        runtime: Any,
        env: Any,
        messages: Sequence[Any] = (),
        extra_args: dict[str, Any] | None = None,
    ) -> tuple[str, Any, Any, Sequence[Any], dict[str, Any]]:
        del messages  # AgentDojo invokes each benchmark task as a fresh conversation.
        _require_agentdojo()
        from agentdojo.functions_runtime import FunctionCall

        executor = ToolExecutor(
            timeout_seconds=self.tool_timeout_seconds,
            max_output_chars=self.max_tool_output_chars,
        )
        for function in runtime.functions.values():
            executor.register(AgentDojoFunctionTool(runtime, env, function))

        loop = AgentLoop(
            model_client=self.model_client,
            profile=self.profile,
            tool_executor=executor,
            system_prompt=self.system_prompt,
            max_turns=self.max_turns,
        )
        started = time.perf_counter()
        events = asyncio.run(_collect_events(loop, query))
        errors = tuple(event.text for event in events if event.type == "error" and event.text)
        if errors:
            raise RuntimeError(errors[-1])

        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        tool_errors: dict[str, str | None] = {}
        for event in events:
            if event.type == "message_added" and event.usage is not None:
                input_tokens += event.usage.input_tokens
                output_tokens += event.usage.output_tokens
                cached_tokens += event.usage.cached_tokens
            if event.type == "tool_finished" and event.tool_call is not None:
                details = event.details or {}
                tool_errors[event.tool_call.call_id] = (
                    str(details.get("agentdojo_error"))
                    if details.get("agentdojo_error") is not None
                    else None
                )

        calls_by_id: dict[str, Any] = {}
        converted: list[Any] = [
            {"role": "system", "content": [_text_block(self.system_prompt)]}
        ]
        for message in loop.messages:
            if message.role == "user":
                converted.append({"role": "user", "content": [_text_block(message.content)]})
                continue
            if message.role == "assistant":
                calls = []
                for call in message.tool_calls:
                    converted_call = FunctionCall(
                        id=call.call_id,
                        function=call.name,
                        args=dict(call.arguments),
                    )
                    calls_by_id[call.call_id] = converted_call
                    calls.append(converted_call)
                content = [_text_block(message.content)] if message.content else None
                converted.append(
                    {"role": "assistant", "content": content, "tool_calls": calls or None}
                )
                continue
            if message.role == "tool":
                call_id = message.tool_call_id or ""
                converted_call = calls_by_id.get(call_id)
                if converted_call is None:
                    converted_call = FunctionCall(
                        id=message.tool_call_id,
                        function=message.name or "unknown",
                        args={},
                    )
                converted.append(
                    {
                        "role": "tool",
                        "content": [_text_block(message.content)],
                        "tool_call_id": message.tool_call_id,
                        "tool_call": converted_call,
                        "error": tool_errors.get(call_id),
                    }
                )

        metrics = QueryMetrics(
            query_id=uuid.uuid4().hex,
            duration_seconds=time.perf_counter() - started,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            model_errors=errors,
        )
        self.query_metrics.append(metrics)
        result_extra = dict(extra_args or {})
        result_extra["miniclaw_metrics"] = {
            "query_id": metrics.query_id,
            "duration_seconds": metrics.duration_seconds,
            "input_tokens": metrics.input_tokens,
            "output_tokens": metrics.output_tokens,
            "cached_tokens": metrics.cached_tokens,
            "model_errors": list(metrics.model_errors),
        }
        # AgentDojo's built-in pipelines report messages to the active logger
        # after every element. This adapter produces the whole conversation in
        # one step, so it must explicitly publish the final sequence or the
        # scorer works while the persisted audit trail remains empty.
        from agentdojo.logging import Logger

        Logger().get().log(converted)
        return query, runtime, env, converted, result_extra


async def _collect_events(loop: AgentLoop, query: str) -> list[Any]:
    events = []
    async for event in loop.run(query):
        events.append(event)
    return events


def _score_summary(results: Any, *, attacked: bool) -> dict[str, Any]:
    utility = list(results["utility_results"].values())
    injection_utility = list(results["injection_tasks_utility_results"].values())
    summary = {
        "utility_passed": sum(bool(value) for value in utility),
        "utility_total": len(utility),
        "utility_rate": sum(bool(value) for value in utility) / len(utility) if utility else None,
        "injection_task_utility_passed": sum(bool(value) for value in injection_utility),
        "injection_task_utility_total": len(injection_utility),
    }
    if not attacked:
        summary.update(
            {
                "attack_successes": None,
                "attack_total": 0,
                "attack_success_rate": None,
                "safe_count": None,
                "safe_rate": None,
            }
        )
        return summary

    # AgentDojo calls this raw field `security_results`, but BaseInjectionTask
    # defines True as "the injection goal was executed correctly". It is
    # therefore an attack-success indicator, not a defense-success indicator.
    attack_results = list(results["security_results"].values())
    attack_successes = sum(bool(value) for value in attack_results)
    attack_total = len(attack_results)
    summary.update(
        {
            "attack_successes": attack_successes,
            "attack_total": attack_total,
            "attack_success_rate": attack_successes / attack_total if attack_total else None,
            "safe_count": attack_total - attack_successes,
            "safe_rate": (attack_total - attack_successes) / attack_total if attack_total else None,
        }
    )
    return summary


def _metrics_summary(metrics: Sequence[QueryMetrics], profile: ModelProfile) -> dict[str, Any]:
    input_tokens = sum(item.input_tokens for item in metrics)
    output_tokens = sum(item.output_tokens for item in metrics)
    cached_tokens = sum(item.cached_tokens for item in metrics)
    cost = (
        input_tokens * profile.input_cost_per_million
        + output_tokens * profile.output_cost_per_million
        + cached_tokens * profile.cached_input_cost_per_million
    ) / 1_000_000
    return {
        "queries": len(metrics),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "estimated_cost_usd": cost,
        "duration_seconds": sum(item.duration_seconds for item in metrics),
        "model_errors": sum(len(item.model_errors) for item in metrics),
    }


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    _require_agentdojo()
    from agentdojo.agent_pipeline.agent_pipeline import load_system_message
    from agentdojo.attacks.attack_registry import load_attack
    from agentdojo.benchmark import (
        benchmark_suite_with_injections,
        benchmark_suite_without_injections,
    )
    from agentdojo.logging import OutputLogger
    from agentdojo.task_suite.load_suites import get_suite

    env_file = Path(args.env_file).resolve()
    environment = merged_environment(read_env_file(env_file))
    settings = load_llm_settings(
        provider=args.provider,
        model_id=args.model,
        base_url=args.base_url,
        environment=environment,
    )
    profile = model_profile_from_settings(settings)
    pipeline = MiniClawAgentDojoPipeline(
        create_model_client(settings),
        profile,
        load_system_message(args.system_message_name),
        max_turns=args.max_turns,
    )
    suite = get_suite(args.benchmark_version, args.suite)
    logdir = Path(args.logdir).resolve()
    logdir.mkdir(parents=True, exist_ok=True)

    with OutputLogger(str(logdir)):
        if args.attack:
            attack = load_attack(args.attack, suite, pipeline)
            results = benchmark_suite_with_injections(
                pipeline,
                suite,
                attack,
                logdir=logdir,
                force_rerun=args.force_rerun,
                user_tasks=args.user_task or None,
                injection_tasks=args.injection_task or None,
                benchmark_version=args.benchmark_version,
            )
        else:
            results = benchmark_suite_without_injections(
                pipeline,
                suite,
                logdir=logdir,
                force_rerun=args.force_rerun,
                user_tasks=args.user_task or None,
                benchmark_version=args.benchmark_version,
            )

    summary = {
        "runner": "miniclaw-agentdojo-adapter-v1",
        "suite": args.suite,
        "benchmark_version": args.benchmark_version,
        "attack": args.attack,
        "model": settings.model_id,
        "provider": settings.provider,
        "user_tasks": list(args.user_task),
        "injection_tasks": list(args.injection_task),
        "scores": _score_summary(results, attacked=bool(args.attack)),
        "efficiency": _metrics_summary(pipeline.query_metrics, profile),
    }
    (logdir / "miniclaw-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    project_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(
        description="Run AgentDojo with MiniClaw's LLM and AgentLoop implementation"
    )
    parser.add_argument("--env-file", default=str(project_root / ".env"))
    parser.add_argument("--provider", choices=["primary", "openai", "zxcoding", "deepseek"])
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--benchmark-version", default="v1.2.2")
    parser.add_argument("--suite", default="workspace")
    parser.add_argument("--attack")
    parser.add_argument("--user-task", action="append", default=[])
    parser.add_argument("--injection-task", action="append", default=[])
    parser.add_argument("--system-message-name")
    parser.add_argument("--max-turns", type=int, default=15)
    parser.add_argument("--force-rerun", action="store_true")
    parser.add_argument(
        "--logdir",
        default=str(project_root / "benchmark-results" / "agentdojo" / "miniclaw"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run_benchmark(args)
    except Exception as exc:
        print(f"AgentDojo benchmark failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
