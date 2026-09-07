from __future__ import annotations

"""ContextWeave adapter for MiniClaw's layered memory implementation.

The official benchmark injects completed user/assistant sessions into an
external memory component and asks it for context before the coding agent
starts.  This module exposes that component protocol and a non-interactive
MiniClaw entry point suitable for the benchmark container.
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from MiniClaw.coding_agent.approval.config import ApprovalSettings
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.memory.manager import (
    RETRIEVAL_TOKEN_BUDGET,
    MemoryManager,
    estimate_retrieval_tokens,
)
from MiniClaw.coding_agent.runtime import load_runtime_settings
from MiniClaw.llm.config import load_llm_settings
from MiniClaw.llm.env_file import merged_environment
from MiniClaw.llm.factory import create_model_client, model_profile_from_settings
from MiniClaw.llm.types import ChatMessage, ModelProfile


class _UnusedModelClient:
    def stream(self, _request: object):  # pragma: no cover - compaction is not used here
        raise RuntimeError("ContextWeave memory preparation does not call a model")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _messages(session: dict[str, Any]) -> list[ChatMessage]:
    result: list[ChatMessage] = []
    for value in session.get("messages") or []:
        if not isinstance(value, dict):
            continue
        role = str(value.get("role") or "")
        content = str(value.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            result.append(ChatMessage(role=role, content=content))  # type: ignore[arg-type]
    return result


def _manager(
    store_root: Path,
    environment: dict[str, str],
    session_id: str = "contextweave-query",
) -> MemoryManager:
    return MemoryManager(
        workspace=store_root,
        session_path=store_root / ".aster" / "sessions" / session_id / "context.jsonl",
        session_id=session_id,
        model_client=_UnusedModelClient(),  # type: ignore[arg-type]
        profile=ModelProfile(model_id="contextweave-memory", context_window=128_000),
        environment=environment,
    )


def prepare_memory_store(
    dataset_path: Path,
    target_index: int,
    store_root: Path,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = _read_json(dataset_path)
    sessions = [item for item in payload.get("sessions") or [] if isinstance(item, dict)]
    selected = sessions[: max(0, target_index - 1)]
    store_root.mkdir(parents=True, exist_ok=True)
    environment = dict(environment or os.environ)
    counts = {"sessions": 0, "archive_chunks": 0, "episodes": 0, "semantic": 0, "procedural": 0}

    raw_sessions: list[dict[str, Any]] = []
    for index, session in enumerate(selected, start=1):
        session_id = str(session.get("session_id") or f"session-{index}")
        messages = _messages(session)
        if not messages:
            continue
        session_environment = {
            **environment,
            "MINICLAW_MEMORY_CONSOLIDATION_ENABLED": "false",
        }
        manager = _manager(store_root, session_environment, session_id)
        counts["sessions"] += 1
        counts["archive_chunks"] += manager.archive.add_messages(
            session_id,
            f"contextweave/{session_id}.jsonl",
            messages,
        )
        result = asyncio.run(
            manager.finalize_session(
                messages,
                status="completed",
            )
        )
        if result.summary:
            counts["episodes"] += 1
        fact_stats = result.details.get("explicit_fact_ingestion") or {}
        counts["semantic"] += fact_stats["remembered"]
        raw_sessions.append(
            {
                "session_id": session_id,
                "messages": [asdict(message) for message in messages],
            }
        )

    raw_path = store_root / ".aster" / "memory" / "contextweave-raw-sessions.json"
    _write_json(raw_path, {"sessions": raw_sessions})
    manifest = {
        "dataset": str(dataset_path.resolve()),
        "target_index": target_index,
        "store_root": str(store_root.resolve()),
        "counts": counts,
        "raw_sessions": str(raw_path),
    }
    _write_json(store_root / "manifest.json", manifest)
    return manifest


def retrieve_memory_context(
    store_root: Path,
    query: str,
    environment: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    environment = dict(environment or os.environ)
    manager = _manager(store_root, environment)
    context = manager.prompt_context(query)
    diagnostics = {
        "mode": "retrieval",
        "context_tokens": estimate_retrieval_tokens(context),
        "retrieval_budget_tokens": RETRIEVAL_TOKEN_BUDGET,
        **manager.last_render_stats,
        "items": manager.retrieval_trace(),
        "ranked_items": manager.ranked_retrieval_trace(),
    }
    return context, diagnostics


def concatenate_memory_context(store_root: Path) -> tuple[str, dict[str, Any]]:
    environment = dict(os.environ)
    manager = _manager(store_root, environment)
    sections: list[str] = []
    semantic = manager.semantic.read()
    if semantic:
        sections.append(f"<semantic_memory>\n{semantic}\n</semantic_memory>")

    episode_parts = [manager.episodic.read(item["sessionId"]) for item in manager.episodic.list(limit=50)]
    if episode_parts:
        sections.append("<episodic_memory>\n" + "\n\n---\n\n".join(episode_parts) + "\n</episodic_memory>")

    procedure_parts = [manager.procedural.read(item.name) for item in manager.procedural.skills.values()]
    if procedure_parts:
        sections.append("<procedural_memory>\n" + "\n\n---\n\n".join(procedure_parts) + "\n</procedural_memory>")

    raw_path = store_root / ".aster" / "memory" / "contextweave-raw-sessions.json"
    raw = _read_json(raw_path)
    transcripts: list[str] = []
    for session in raw.get("sessions") or []:
        if not isinstance(session, dict):
            continue
        parts = [f"Session: {session.get('session_id', '')}"]
        for message in session.get("messages") or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "message")
            content = str(message.get("content") or "").strip()
            if content:
                parts.append(f"[{role}]\n{content}")
        transcripts.append("\n\n".join(parts))
    if transcripts:
        sections.append("<working_archive>\n" + "\n\n=====\n\n".join(transcripts) + "\n</working_archive>")

    context = "\n\n".join(sections)
    return context, {
        "mode": "concat",
        "context_tokens": estimate_retrieval_tokens(context),
        "retrieval_budget_tokens": None,
        "sections": len(sections),
        "sessions": len(transcripts),
    }


def plugin_inject(args: argparse.Namespace) -> int:
    run_dir = Path(os.environ.get("MEMBENCH_RUN_DIR") or ".").resolve()
    store_root = run_dir / "miniclaw_store"
    manifest = prepare_memory_store(Path(args.dataset), args.target_index, store_root)
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


def plugin_recall(args: argparse.Namespace) -> int:
    run_dir = Path(os.environ.get("MEMBENCH_RUN_DIR") or ".").resolve()
    store_root = run_dir / "miniclaw_store"
    query = Path(args.query_file).read_text(encoding="utf-8").strip()
    mode = os.environ.get("MINICLAW_CONTEXTWEAVE_MODE", "retrieval").strip().casefold()
    if mode == "concat":
        context, diagnostics = concatenate_memory_context(store_root)
    else:
        context, diagnostics = retrieve_memory_context(store_root, query)
    _write_json(run_dir / "miniclaw_memory_diagnostics.json", diagnostics)
    print(json.dumps({"additional_context": context}, ensure_ascii=False))
    return 0


async def run_agent(prompt: str) -> int:
    environment = merged_environment(dict(os.environ))
    settings = load_llm_settings(
        provider=environment.get("MINICLAW_PROVIDER") or "primary",
        model_id=environment.get("MINICLAW_PRIMARY_MODEL") or "gpt-5.6-luna",
        environment=environment,
    )
    runtime_settings = load_runtime_settings(
        environment,
        sandbox="host",
        workspace_mode="direct",
    )
    approval = ApprovalSettings(policy="allow", timeout_seconds=300.0)
    assistant = CodingAssistant(
        model_client=create_model_client(settings),
        profile=model_profile_from_settings(settings),
        workspace=Path("/workspace"),
        runtime_settings=runtime_settings,
        approval_settings=approval,
        environment=environment,
        trace_provider=settings.provider,
        trace_channel="benchmark-contextweave",
    )
    had_error = False
    async for event in assistant.run(prompt):
        if event.type == "text_delta":
            print(event.text, end="", flush=True)
        elif event.type == "tool_started" and event.tool_call:
            print(f"\n[tool] {event.tool_call.name}", flush=True)
        elif event.type == "error":
            had_error = True
            print(f"\n[error] {event.text}", flush=True)
    print(flush=True)
    return 1 if had_error else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MiniClaw ContextWeave benchmark adapter")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inject = subparsers.add_parser("plugin-inject")
    inject.add_argument("--dataset", required=True)
    inject.add_argument("--target-index", required=True, type=int)

    recall = subparsers.add_parser("plugin-recall")
    recall.add_argument("--query-file", required=True)

    subparsers.add_parser("agent")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "plugin-inject":
        raise SystemExit(plugin_inject(args))
    if args.command == "plugin-recall":
        raise SystemExit(plugin_recall(args))
    prompt = sys.stdin.read().strip()
    if not prompt:
        raise SystemExit("ContextWeave agent requires a prompt on stdin")
    raise SystemExit(asyncio.run(run_agent(prompt)))


if __name__ == "__main__":
    main()
