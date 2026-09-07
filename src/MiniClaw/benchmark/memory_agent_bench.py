from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from MiniClaw.agent.loop import AgentLoop
from MiniClaw.llm.config import load_llm_settings
from MiniClaw.llm.env_file import merged_environment, read_env_file
from MiniClaw.llm.factory import create_model_client
from MiniClaw.llm.types import ChatMessage, ModelProfile
from MiniClaw.coding_agent.memory.config import MemoryConfig, load_memory_config
from MiniClaw.coding_agent.memory.manager import MemoryManager
from MiniClaw.coding_agent.memory.retrieval import (
    MemoryDocument,
    create_hybrid_memory_retriever,
)
from MiniClaw.coding_agent.memory.query_tracker import QueryTracker, attach_search_metadata
from MiniClaw.coding_agent.memory.working import estimate_context_tokens
from MiniClaw.coding_agent.tools.base import ToolResult
from MiniClaw.coding_agent.tools.executor import ToolExecutor
from MiniClaw.trace.model_client import TracingModelClient, reset_active_run, set_active_run
from MiniClaw.trace.store import TraceRecorder


_NORMALIZE = re.compile(r"[^a-z0-9]+")
_VERSIONED_FACT_LINE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")
_RELATION_PHRASES = tuple(
    sorted(
        (
            " is associated with the sport of ",
            " was created in the country of ",
            " is located in the continent of ",
            " plays the position of ",
            " is affiliated with the religion of ",
            " was founded in the city of ",
            " was born in the city of ",
            " died in the city of ",
            " worked in the city of ",
            " works in the field of ",
            " was written in the language of ",
            " speaks the language of ",
            " is married to ",
            " is employed by ",
            " was founded by ",
            " was developed by ",
            " was performed by ",
            " was created by ",
            " is famous for ",
        ),
        key=len,
        reverse=True,
    )
)


@dataclass(slots=True, frozen=True)
class BenchmarkCase:
    case_id: str
    source: str
    context: str
    question: str
    answers: tuple[str, ...]


def _fact_parts(content: str) -> tuple[str, str, str]:
    text = content.strip().rstrip(".")
    normalized = text.casefold()
    for phrase in _RELATION_PHRASES:
        index = normalized.find(phrase)
        if index > 0:
            value_start = index + len(phrase)
            return text[:index].strip(), phrase.strip(), text[value_start:].strip()
    for phrase in (" is ", " was "):
        index = normalized.find(phrase)
        if index > 0:
            value_start = index + len(phrase)
            return text[:index].strip(), phrase.strip(), text[value_start:].strip()
    return text, "", ""


def parse_fact_documents(context: str) -> list[MemoryDocument]:
    documents: list[MemoryDocument] = []
    for raw_line in context.splitlines():
        match = _VERSIONED_FACT_LINE.match(raw_line)
        if not match:
            continue
        fact_id, content = match.groups()
        subject, relation, value = _fact_parts(content)
        documents.append(
            MemoryDocument(
                record_id=f"fact_{fact_id}",
                category="fact",
                content=content,
                subject=subject,
                relation=relation,
                value=value,
                version=int(fact_id),
            )
        )
    return documents


def normalize_answer(value: str) -> str:
    return _NORMALIZE.sub(" ", value.casefold()).strip()


def score_answer(output: str, answers: tuple[str, ...]) -> dict[str, Any]:
    prediction = normalize_answer(output.removeprefix("FINAL:").strip())
    expected = tuple(normalize_answer(answer) for answer in answers if answer.strip())
    exact = prediction in expected
    substring = any(answer and answer in prediction for answer in expected)
    return {
        "exact_match": exact,
        "substring_exact_match": substring,
        "prediction": prediction,
        "expected": list(expected),
    }


def benchmark_memory_config(profile: ModelProfile, scaled: bool) -> MemoryConfig:
    if not scaled:
        return load_memory_config(profile)
    # Evaluation-only 1:10 version of the production 80k/100k/30k/20k profile.
    # Reserve and deterministic semantic budgets are reduced by the same factor.
    return MemoryConfig(
        enabled=True,
        # A strict 1:10 reserve (1,638) caps checkpoint generation at 1,310
        # tokens in WorkingContext, which is below the 3k checkpoint target and
        # repeatedly truncates real provider summaries. Keep the trigger/target/
        # recent-history ratios at 1:10, but give the benchmark summarizer a
        # practical 2k output budget.
        reserve_tokens=2_560,
        keep_recent_tokens=2_000,
        soft_trigger_tokens=8_000,
        hard_trigger_tokens=10_000,
        target_tokens=3_000,
        progressive_enabled=True,
        artifact_threshold_bytes=16 * 1024,
        artifact_preview_chars=4_000,
        deterministic_semantic_tokens=450,
    )


class FactSearchTool:
    name = "search_memory"
    description = (
        "Search the benchmark memory's active facts. Older facts with the same structured subject "
        "and relation are resolved before ranking and listed under supersedes. Use repeated searches "
        "for multi-hop questions: first search the named entity, then search entities discovered in "
        "returned facts. Only returned active facts may be used as evidence."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        documents: list[MemoryDocument],
        *,
        default_limit: int = 10,
        recorder: TraceRecorder | None = None,
        run_id: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.documents = documents
        self.default_limit = default_limit
        self.retriever = create_hybrid_memory_retriever(environment)
        self.recorder = recorder
        self.run_id = run_id
        self.query_tracker = QueryTracker()

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        started = time.perf_counter()
        query = str(arguments["query"])
        limit = min(30, int(arguments.get("limit", self.default_limit)))
        cached = self.query_tracker.cached("benchmark", query, limit)
        if cached is not None:
            progress = self.query_tracker.cache_progress(cached)
            payload = cached.payload
        else:
            hits = self.retriever.search(query, self.documents, limit)
            payload = [
                {
                    "record_id": hit.document.record_id,
                    "content": hit.document.content,
                    "supersedes": list(hit.superseded_record_ids),
                    "score": round(hit.score, 6),
                    "bm25_rank": hit.bm25_rank,
                    "vector_rank": hit.vector_rank,
                    "deterministic_score": round(hit.deterministic_score, 6),
                    "cross_encoder_score": (
                        round(hit.cross_encoder_score, 6)
                        if hit.cross_encoder_score is not None
                        else None
                    ),
                }
                for hit in hits
            ]
            progress = self.query_tracker.observe(
                "benchmark",
                query,
                limit,
                {str(item["record_id"]) for item in payload},
                payload,
            )
        rendered_payload = attach_search_metadata(payload, progress)
        if self.recorder:
            self.recorder.record(
                "tool.call",
                {
                    "tool": self.name,
                    "status": "success",
                    "arguments": {"query": query, "limit": limit},
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                    "result_count": len(payload),
                    "query_dedup": progress.metadata(),
                    "reranker": dict(self.retriever.last_diagnostics),
                },
                run_id=self.run_id,
            )
        return ToolResult(
            json.dumps(rendered_payload, ensure_ascii=False, separators=(",", ":")),
            details={"query_dedup": progress.metadata()},
        )


def _answers(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        flattened: list[str] = []
        for item in value:
            if isinstance(item, (list, tuple)):
                flattened.extend(str(child) for child in item)
            elif item is not None:
                flattened.append(str(item))
        return tuple(flattened)
    return (str(value),) if value is not None else ()


def load_cases(path: Path, source: str, limit: int, offset: int = 0) -> list[BenchmarkCase]:
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:  # pragma: no cover - depends on optional benchmark runtime.
        raise RuntimeError("MemoryAgentBench requires pyarrow: pip install pyarrow") from exc
    rows = parquet.read_table(path).to_pylist()
    row = next(
        (item for item in rows if (item.get("metadata") or {}).get("source") == source),
        None,
    )
    if row is None:
        available = sorted((item.get("metadata") or {}).get("source", "") for item in rows)
        raise ValueError(f"Unknown source {source!r}; available: {', '.join(available)}")
    questions = list(row.get("questions") or [])
    answers = list(row.get("answers") or [])
    metadata = row.get("metadata") or {}
    ids = list(metadata.get("qa_pair_ids") or [])
    stop = min(len(questions), offset + max(1, limit))
    cases: list[BenchmarkCase] = []
    for index in range(offset, stop):
        cases.append(
            BenchmarkCase(
                case_id=str(ids[index] if index < len(ids) else f"{source}_{index}"),
                source=source,
                context=str(row["context"]),
                question=str(questions[index]),
                answers=_answers(answers[index] if index < len(answers) else []),
            )
        )
    return cases


def _profile(settings: Any, max_output_tokens: int = 2_048) -> ModelProfile:
    return ModelProfile(
        model_id=settings.model_id,
        context_window=settings.context_window,
        max_output_tokens=min(max_output_tokens, settings.max_output_tokens),
        input_cost_per_million=settings.input_cost_per_million,
        output_cost_per_million=settings.output_cost_per_million,
        cached_input_cost_per_million=settings.cached_input_cost_per_million,
    )


def _chunks(context: str, target_chars: int = 3_600) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_chars = 0
    for line in context.splitlines():
        line_size = len(line) + 1
        if current and current_chars + line_size > target_chars:
            chunks.append("\n".join(current))
            current, current_chars = [], 0
        current.append(line)
        current_chars += line_size
    if current:
        chunks.append("\n".join(current))
    return chunks


async def run_hybrid_case(
    case: BenchmarkCase,
    *,
    client: Any,
    profile: ModelProfile,
    recorder: TraceRecorder,
    run_id: str,
    retrieval_limit: int,
    environment: dict[str, str],
) -> dict[str, Any]:
    documents = parse_fact_documents(case.context)
    executor = ToolExecutor(timeout_seconds=30, max_output_chars=50_000)
    executor.register(
        FactSearchTool(
            documents,
            default_limit=retrieval_limit,
            recorder=recorder,
            run_id=run_id,
            environment=environment,
        )
    )
    loop = AgentLoop(
        client,
        profile,
        executor,
        max_turns=10,
        system_prompt=(
            "Pretend you are a knowledge management system. Every search result has a fact_N "
            "record id, and a larger N means the fact is newer. Resolve every conflict by using "
            "the newest relevant fact with the largest serial number, even when it contradicts "
            "real-world knowledge. search_memory already removes older versions from each exact "
            "subject/relation conflict group and reports them in supersedes; always follow the "
            "returned active value. Search iteratively for every hop and use only search_memory "
            "evidence. Your entire final response must be exactly 'FINAL: <short answer>' with no "
            "reasoning, citations, alternatives, or explanation."
        ),
    )
    final_text = ""
    error = ""
    token = set_active_run(run_id)
    try:
        async for event in loop.run(case.question):
            if event.type == "run_finished" and event.message:
                final_text = event.message.content
            elif event.type == "error":
                error = event.text
    finally:
        reset_active_run(token)
    scored = score_answer(final_text, case.answers)
    return {
        "case_id": case.case_id,
        "source": case.source,
        "mode": "hybrid",
        "question": case.question,
        "answers": list(case.answers),
        "output": final_text,
        "error": error,
        "documents": len(documents),
        **scored,
        "metrics": recorder.run_metrics(run_id),
    }


async def run_compaction_case(
    case: BenchmarkCase,
    *,
    client: Any,
    profile: ModelProfile,
    recorder: TraceRecorder,
    run_id: str,
    case_dir: Path,
    scaled: bool,
    environment: dict[str, str],
) -> dict[str, Any]:
    config = benchmark_memory_config(profile, scaled)
    manager = MemoryManager(
        workspace=case_dir,
        session_path=case_dir / "transcript.jsonl",
        session_id=case.case_id,
        model_client=client,
        profile=profile,
        config=config,
        environment=environment,
    )
    working = manager.working
    messages: list[ChatMessage] = []
    compactions: list[dict[str, Any]] = []
    token = set_active_run(run_id)
    try:
        for index, chunk in enumerate(_chunks(case.context), start=1):
            user = ChatMessage(role="user", content=f"Memory update {index}:\n{chunk}")
            assistant = ChatMessage(role="assistant", content=f"Stored memory update {index}.")
            for message in (user, assistant):
                messages.append(message)
                manager.append(message)
            outcome = await working.maybe_compact(messages, estimate_context_tokens(messages))
            if outcome:
                messages = outcome.messages
                item = {
                    "tokens_before": outcome.tokens_before,
                    "tokens_after": outcome.estimated_tokens_after,
                    "strategy": outcome.strategy,
                    "details": outcome.details,
                }
                compactions.append(item)
                recorder.record("compaction.completed", item, run_id=run_id)
        memory_context = manager.prompt_context(case.question)
        recorder.record(
            "memory.retrieval",
            {
                "query": case.question,
                "injected_count": manager.last_rendered_count,
                **manager.last_render_stats,
                "items": manager.retrieval_trace(),
            },
            run_id=run_id,
        )
        executor = ToolExecutor(timeout_seconds=30, max_output_chars=50_000)
        for tool in manager.tools():
            executor.register(tool)
        loop = AgentLoop(
            client,
            profile,
            executor,
            max_turns=10,
            system_prompt=(
                "Answer a MemoryAgentBench question using only retrieved_memory and the memory "
                "tool. Larger fact_N serial numbers are newer. archive_search already removes older "
                "facts from the same subject/relation conflict group. Search iteratively for every "
                "missing hop and never use real-world knowledge. Your entire final response must be "
                "exactly 'FINAL: <short answer>' with no explanation.\n\n"
                f"{memory_context}"
            ),
        )
        loop.messages = list(messages)
        final_text = ""
        error = ""
        async for event in loop.run(case.question):
            if event.type == "message_added" and event.message is not None:
                manager.append(event.message)
            elif event.type == "tool_finished" and event.tool_call is not None:
                recorder.record(
                    "tool.call",
                    {
                        "tool": event.tool_call.name,
                        "status": "error" if event.is_error else "success",
                        "arguments": event.tool_call.arguments,
                        "result": event.tool_result if not event.is_error else None,
                        "error": event.tool_result if event.is_error else None,
                    },
                    run_id=run_id,
                )
            elif event.type == "run_finished" and event.message is not None:
                final_text = event.message.content
            elif event.type == "error":
                error = event.text
    except Exception as exc:
        final_text = ""
        error = f"{type(exc).__name__}: {exc}"
    finally:
        reset_active_run(token)
    return {
        "case_id": case.case_id,
        "source": case.source,
        "mode": "compaction_scaled" if scaled else "compaction_production",
        "question": case.question,
        "answers": list(case.answers),
        "output": final_text,
        "error": error,
        "compaction_profile": asdict(config),
        "compactions": compactions,
        **score_answer(final_text, case.answers),
        "metrics": recorder.run_metrics(run_id),
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


async def run(args: argparse.Namespace) -> int:
    file_values = read_env_file(args.env_file) if args.env_file else {}
    environment = merged_environment(file_values)
    settings = load_llm_settings(
        provider=args.provider or file_values.get("MINICLAW_PROVIDER"),
        model_id=args.model,
        base_url=args.base_url,
        environment=environment,
    )
    profile = _profile(settings, args.max_output_tokens)
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    recorder = TraceRecorder(output_dir, "benchmark", "memory-agent-bench")
    client = TracingModelClient(create_model_client(settings), recorder, settings.provider)
    cases = load_cases(Path(args.data), args.source, args.limit, args.offset)
    results: list[dict[str, Any]] = []
    started = time.perf_counter()
    for position, case in enumerate(cases, start=1):
        run_id = recorder.new_run_id()
        recorder.record(
            "benchmark.case.started",
            {
                "benchmark": "MemoryAgentBench",
                "case_id": case.case_id,
                "source": case.source,
                "mode": args.mode,
                "position": position,
                "total": len(cases),
            },
            run_id=run_id,
        )
        case_dir = output_dir / "cases" / case.case_id
        if args.mode == "hybrid":
            result = await run_hybrid_case(
                case,
                client=client,
                profile=profile,
                recorder=recorder,
                run_id=run_id,
                retrieval_limit=args.retrieval_limit,
                environment=environment,
            )
        else:
            result = await run_compaction_case(
                case,
                client=client,
                profile=profile,
                recorder=recorder,
                run_id=run_id,
                case_dir=case_dir,
                scaled=args.compaction_profile == "scaled",
                environment=environment,
            )
        results.append(result)
        recorder.record("benchmark.case.completed", result, run_id=run_id)
        _write_json(output_dir / "results.json", results)
        print(
            json.dumps(
                {
                    "case": case.case_id,
                    "mode": result["mode"],
                    "exact": result["exact_match"],
                    "substring": result["substring_exact_match"],
                    "output": result["output"],
                    "error": result["error"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    successful = sum(bool(item["substring_exact_match"]) for item in results)
    totals = {
        "benchmark": "MemoryAgentBench",
        "provider": settings.provider,
        "model": settings.model_id,
        "source": args.source,
        "mode": args.mode,
        "cases": len(results),
        "substring_correct": successful,
        "substring_accuracy": successful / max(1, len(results)),
        "exact_correct": sum(bool(item["exact_match"]) for item in results),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "results_path": str(output_dir / "results.json"),
        "trace_path": str(recorder.path),
    }
    _write_json(output_dir / "summary.json", totals)
    print(json.dumps(totals, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run MiniClaw on MemoryAgentBench")
    parser.add_argument("--data", required=True, help="Official MemoryAgentBench parquet file")
    parser.add_argument("--source", default="factconsolidation_mh_6k")
    parser.add_argument("--mode", choices=["hybrid", "compaction"], default="hybrid")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--retrieval-limit", type=int, default=10)
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=2_048,
        help=(
            "Per-turn model output budget. The scaled compaction profile needs more than a 1k "
            "ceiling, while 2k keeps the pilot inexpensive."
        ),
    )
    parser.add_argument("--compaction-profile", choices=["scaled", "production"], default="scaled")
    parser.add_argument("--output", default=".aster/benchmarks/results/memory-agent-bench")
    parser.add_argument("--env-file")
    parser.add_argument("--provider", choices=["primary", "openai", "zxcoding", "deepseek"])
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(asyncio.run(run(build_parser().parse_args())))


if __name__ == "__main__":
    main()
