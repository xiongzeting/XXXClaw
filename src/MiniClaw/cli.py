from __future__ import annotations

import argparse
import asyncio
import signal
from pathlib import Path

from .coding_agent.approval import cli_approval_handler, load_approval_settings
from .coding_agent.assistant.coding import CodingAssistant
from .coding_agent.goal.prompts import parse_goal_command
from .coding_agent.runtime import load_runtime_settings
from .llm.config import load_llm_settings
from .llm.env_file import merged_environment, read_env_file
from .llm.factory import create_model_client, model_profile_from_settings


async def interactive(args: argparse.Namespace) -> None:
    env_path = Path(args.env_file) if args.env_file else Path.cwd() / ".env"
    file_values = read_env_file(env_path) if env_path.is_file() else {}
    environment = merged_environment(file_values)
    try:
        settings = load_llm_settings(
            provider=args.provider,
            model_id=args.model,
            base_url=args.base_url,
            environment=environment,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    model_client = create_model_client(settings)
    try:
        runtime_settings = load_runtime_settings(
            environment,
            sandbox=args.sandbox,
            workspace_mode=args.workspace_mode,
            docker_image=args.docker_image,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        approval_settings = load_approval_settings(
            args.workspace,
            environment,
            policy=args.approval_policy,
            timeout_seconds=args.approval_timeout,
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    try:
        assistant = CodingAssistant(
            model_client=model_client,
            profile=model_profile_from_settings(settings),
            workspace=args.workspace,
            runtime_settings=runtime_settings,
            approval_settings=approval_settings,
            environment=environment,
            approval_handler=lambda request: cli_approval_handler(
                request,
                approval_settings.timeout_seconds,
            ),
            trace_provider=settings.provider,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"MiniClaw workspace: {Path(args.workspace).resolve()}")
    print(f"Provider: {settings.provider} | Model: {settings.model_id} | API: {settings.base_url}")
    print(
        f"Streaming: SSE | Timeouts connect/first/idle/total: "
        f"{settings.connect_timeout_seconds:g}/{settings.first_token_timeout_seconds:g}/"
        f"{settings.idle_timeout_seconds:g}/{settings.timeout_seconds:g}s | "
        f"Retries: {settings.max_retries} | Fallbacks: {len(settings.fallbacks)}"
    )
    print(
        f"Runtime: {runtime_settings.sandbox} | Workspace mode: {runtime_settings.workspace_mode} | "
        f"Tool workspace: {assistant.runtime.execution_workspace}"
    )
    print(
        f"Approval: {approval_settings.policy} | Timeout: "
        f"{approval_settings.timeout_seconds:g}s | Allowlist: {len(approval_settings.allowlist)}"
    )
    print(assistant.instruction_status())
    print("Type /exit to quit. Press Ctrl+C during a run to cancel model and tool execution.")

    def handle_interrupt(_signum, _frame) -> None:
        if assistant.cancel("Task cancelled from CLI"):
            print("\n[cancel] 正在终止模型、工具和运行进程…", flush=True)
            return
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handle_interrupt)

    while True:
        try:
            prompt = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not prompt:
            continue
        if prompt == "/exit":
            return
        command = parse_goal_command(prompt)
        if command and command.action == "status":
            print(assistant.goal_status())
            continue
        if command and command.action == "cancel":
            cancelled = assistant.cancel(command.reason or "Goal cancelled by the user")
            print(assistant.goal_status() if cancelled else "当前没有可取消的任务。")
            continue
        if command and command.action == "start":
            assistant.create_goal(command.goal or "", command.acceptance_criteria or [])
            events = assistant.run_goal(prompt)
        elif command and command.action == "resume":
            assistant.goal_store.resume()
            events = assistant.run_goal()
        else:
            events = assistant.run(prompt)
        async for event in events:
            if event.type == "text_delta":
                print(event.text, end="", flush=True)
            elif event.type == "tool_started" and event.tool_call:
                print(f"\n[tool] {event.tool_call.name}")
            elif event.type == "error":
                print(f"\n[error] {event.text}")
            elif event.type == "run_finished" and event.details:
                for artifact in event.details.get("artifacts") or []:
                    if isinstance(artifact, dict) and artifact.get("path"):
                        print(f"\n[artifact] {artifact['path']}")
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="miniclaw", description="Run the MiniClaw coding assistant")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--provider", choices=["primary", "openai", "zxcoding", "deepseek"])
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--env-file", help="MiniClaw dotenv file; defaults to ./.env when present")
    parser.add_argument(
        "--sandbox",
        help="Command runtime: host, docker, or docker:<image>",
    )
    parser.add_argument("--workspace-mode", choices=["direct", "snapshot"])
    parser.add_argument("--docker-image")
    parser.add_argument("--approval-policy", choices=["allow", "ask", "deny"])
    parser.add_argument("--approval-timeout", type=float)
    return parser


def main() -> None:
    asyncio.run(interactive(build_parser().parse_args()))


if __name__ == "__main__":
    main()
