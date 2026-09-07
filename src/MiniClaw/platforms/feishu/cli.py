from __future__ import annotations

import argparse
from pathlib import Path

from MiniClaw.coding_agent.approval import load_approval_settings
from MiniClaw.coding_agent.goal.config import load_goal_config, load_goal_judge_config
from MiniClaw.llm.config import load_llm_settings
from MiniClaw.llm.factory import create_model_client, model_profile_from_settings
from MiniClaw.llm.env_file import merged_environment, read_env_file
from MiniClaw.coding_agent.runtime import load_runtime_settings, validate_docker_environment

from .bot import FeishuBot
from .config import load_feishu_settings
from .router import FeishuAssistantRouter
from .transport import FeishuTransport


def run(args: argparse.Namespace) -> None:
    env_path = Path(args.env_file) if args.env_file else Path.cwd() / ".env"
    file_values = read_env_file(env_path) if env_path.is_file() else {}
    environment = merged_environment(file_values)
    llm_settings = load_llm_settings(environment=environment)
    feishu_settings = load_feishu_settings(environment)
    runtime_settings = load_runtime_settings(
        environment,
        sandbox=args.sandbox,
        workspace_mode=args.workspace_mode,
        docker_image=args.docker_image,
    )
    approval_settings = load_approval_settings(
        workspace=args.workspace,
        environment=environment,
        policy=args.approval_policy,
        timeout_seconds=args.approval_timeout,
    )
    validate_docker_environment(runtime_settings)
    profile = model_profile_from_settings(llm_settings)
    model_client = create_model_client(llm_settings)
    workspace = Path(args.workspace).resolve()
    transport = FeishuTransport(feishu_settings)
    try:
        router = FeishuAssistantRouter(
            workspace=workspace,
            model_client=model_client,
            profile=profile,
            settings=feishu_settings,
            transport=transport,
            goal_config=load_goal_config(environment),
            goal_judge_config=load_goal_judge_config(environment),
            runtime_settings=runtime_settings,
            approval_settings=approval_settings,
            trace_provider=llm_settings.provider,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"MiniClaw Feishu workspace: {workspace}")
    print(
        f"LLM: {llm_settings.provider}/{llm_settings.model_id} | "
        f"API: {llm_settings.base_url} | Session scope: {feishu_settings.session_scope}"
    )
    print(
        f"Streaming: SSE | Timeouts connect/first/idle/total: "
        f"{llm_settings.connect_timeout_seconds:g}/{llm_settings.first_token_timeout_seconds:g}/"
        f"{llm_settings.idle_timeout_seconds:g}/{llm_settings.timeout_seconds:g}s | "
        f"Retries: {llm_settings.max_retries} | Fallbacks: {len(llm_settings.fallbacks)}"
    )
    print(
        f"Runtime: {runtime_settings.sandbox} | Workspace mode: {runtime_settings.workspace_mode}"
    )
    print(
        f"Approval: {approval_settings.policy} | Timeout: "
        f"{approval_settings.timeout_seconds:g}s | Allowlist: {len(approval_settings.allowlist)}"
    )
    print("Connecting through the official Feishu SDK long connection...")
    bot = FeishuBot(feishu_settings, router)
    try:
        bot.start()
    except KeyboardInterrupt:
        print("MiniClaw Feishu connection stopped.")
    finally:
        bot.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run MiniClaw through Feishu long connection")
    parser.add_argument("--workspace", default=".")
    parser.add_argument(
        "--env-file",
        help="Load a MiniClaw dotenv file",
    )
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
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()
