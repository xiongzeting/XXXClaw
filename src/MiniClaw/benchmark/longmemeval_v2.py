from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import re
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any, BinaryIO

from MiniClaw.agent.loop import AgentLoop
from MiniClaw.coding_agent.memory.retrieval import (
    HybridMemoryRetriever,
    MemoryDocument,
    create_hybrid_memory_retriever,
)
from MiniClaw.coding_agent.memory.manager import (
    estimate_retrieval_tokens,
    truncate_retrieval_text,
)
from MiniClaw.coding_agent.tools.base import ToolResult
from MiniClaw.coding_agent.tools.executor import ToolExecutor
from MiniClaw.llm.config import load_llm_settings
from MiniClaw.llm.env_file import merged_environment, read_env_file
from MiniClaw.llm.factory import create_model_client
from MiniClaw.llm.types import ChatMessage, ModelProfile, ModelRequest, TokenUsage
from MiniClaw.trace.model_client import TracingModelClient, reset_active_run, set_active_run
from MiniClaw.trace.store import TraceRecorder

from .memory_agent_bench import _profile


QUESTION_TYPES = (
    "static-environment",
    "static-environment-abs",
    "dynamic-environment",
    "dynamic-environment-abs",
    "procedure",
    "procedure-abs",
    "errors-gotchas",
)
DOMAINS = ("enterprise", "web")
LLM_EVAL_FUNCTIONS = {"llm_abstention_checker", "llm_gotchas_checker"}
_TRAJECTORY_ID = re.compile(br'^\{"id":"([^"]+)"')
_LATEX_TEXT_WRAPPER = re.compile(
    r"^\\(?:text|mathrm|operatorname|mathbf|mathit|texttt)\s*\{(.*)\}$",
    re.DOTALL,
)
PARENT_RETRIEVAL_LIMIT = 5
MEMORY_CONTEXT_TOKEN_BUDGET = 15_000
AGENTIC_MAX_SEARCHES = 3
AGENTIC_MAX_READS = 5
AGENTIC_MAX_TURNS = 8

DOMAIN_SYSTEM_PROMPTS = {
    "web": (
        "You are an experienced colleague in a web browsing environment that has "
        "a customized magento-based shopping website, a customized magento-based "
        "shopping admin cms website, as well as a customized forum website based "
        "on reddit/postmill. Answer based on your memory of the environment. "
        "If you do not know the answer, output exactly \\boxed{UNKNOWN}. "
        "Do not guess. Never attempt to guess an answer if you are not sure. "
        "If you believe the question's construction/premise is wrong, provide an "
        "explanation in \\boxed{} explaining why the question is flawed."
    ),
    "enterprise": (
        "You are an experienced colleague working in a customized ServiceNow "
        "environment. Answer based on your memory of the environment. "
        "If you do not know the answer, output exactly \\boxed{UNKNOWN}. "
        "Do not guess. Never attempt to guess an answer if you are not sure. "
        "If you believe the question's construction/premise is wrong, provide an "
        "explanation in \\boxed{} explaining why the question is flawed."
    ),
}


@dataclass(slots=True, frozen=True)
class SelectedQuestion:
    question_id: str
    domain: str
    question_type: str
    question: str
    answer: str
    eval_function: str


@dataclass(slots=True, frozen=True)
class TrajectoryOffset:
    offset: int
    length: int


@dataclass(slots=True, frozen=True)
class TrajectoryChunkMetadata:
    trajectory_id: str
    state_index: int | None
    source_field: str
    chunk_index: int


@dataclass(slots=True, frozen=True)
class RetrievedParent:
    parent_id: str
    trajectory_id: str
    anchor_state_index: int | None
    anchor_record_id: str
    record_ids: tuple[str, ...]
    state_indices: tuple[int, ...]
    content: str
    score: float
    bm25_score: float
    vector_score: float
    bm25_rank: int | None
    vector_rank: int | None
    deterministic_score: float = 0.0
    cross_encoder_score: float | None = None


class TrajectoryMemoryIndex:
    """One case's searchable trajectory corpus, built once and queried repeatedly."""

    def __init__(
        self,
        *,
        trajectory_ids: list[str],
        trajectory_path: Path,
        offsets: dict[str, TrajectoryOffset],
        chunk_chars: int,
        retriever: HybridMemoryRetriever | None = None,
    ) -> None:
        self.trajectory_ids = tuple(trajectory_ids)
        self.retriever = retriever or HybridMemoryRetriever()
        self.documents: list[MemoryDocument] = []
        self.metadata: dict[str, TrajectoryChunkMetadata] = {}
        with trajectory_path.open("rb") as handle:
            for trajectory_id in trajectory_ids:
                trajectory = read_trajectory(handle, trajectory_id, offsets)
                values, value_metadata = trajectory_documents(
                    trajectory,
                    chunk_chars=chunk_chars,
                )
                self.documents.extend(values)
                self.metadata.update(value_metadata)

        self.documents_by_parent: dict[
            tuple[str, int | None], list[MemoryDocument]
        ] = defaultdict(list)
        state_values: dict[str, set[int]] = defaultdict(set)
        for document in self.documents:
            item = self.metadata[document.record_id]
            self.documents_by_parent[(item.trajectory_id, item.state_index)].append(document)
            if item.state_index is not None:
                state_values[item.trajectory_id].add(item.state_index)
        self.state_order = {
            trajectory_id: sorted(values) for trajectory_id, values in state_values.items()
        }

    def search(
        self,
        query: str,
        *,
        limit: int = PARENT_RETRIEVAL_LIMIT,
        maximum_limit: int = PARENT_RETRIEVAL_LIMIT,
    ) -> tuple[list[RetrievedParent], dict[str, Any]]:
        started = time.perf_counter()
        parent_limit = max(1, min(limit, maximum_limit))
        candidate_limit = min(len(self.documents), max(40, parent_limit * 10))
        hits = self.retriever.search(query, self.documents, candidate_limit)
        duration = time.perf_counter() - started

        selected: list[tuple[Any, TrajectoryChunkMetadata]] = []
        selected_parents: set[tuple[str, int | None]] = set()
        for hit in hits:
            item = self.metadata[hit.document.record_id]
            key = (item.trajectory_id, item.state_index)
            if key in selected_parents:
                continue
            selected.append((hit, item))
            selected_parents.add(key)
            if len(selected) >= parent_limit:
                break

        retrieved: list[RetrievedParent] = []
        for hit, anchor in selected:
            if anchor.state_index is None:
                parent_keys = [(anchor.trajectory_id, None)]
            else:
                ordered_states = self.state_order[anchor.trajectory_id]
                position = ordered_states.index(anchor.state_index)
                parent_keys = [
                    (anchor.trajectory_id, state_index)
                    for state_index in ordered_states[max(0, position - 1) : position + 2]
                ]

            selected_documents: list[MemoryDocument] = []
            for parent_key in parent_keys:
                for document in self.documents_by_parent.get(parent_key, []):
                    item = self.metadata[document.record_id]
                    if item.source_field in {"trajectory", "state"}:
                        selected_documents.append(document)
                    elif abs(item.chunk_index - anchor.chunk_index) <= 1:
                        selected_documents.append(document)

            sections: list[str] = []
            for parent_key in parent_keys:
                parent_documents = [
                    document
                    for document in selected_documents
                    if (
                        self.metadata[document.record_id].trajectory_id,
                        self.metadata[document.record_id].state_index,
                    )
                    == parent_key
                ]
                if not parent_documents:
                    continue
                state_index = parent_key[1]
                state_label = (
                    "trajectory-summary" if state_index is None else f"state-{state_index}"
                )
                body = "\n".join(
                    f"[{self.metadata[document.record_id].source_field}; chunk="
                    f"{self.metadata[document.record_id].chunk_index}]\n{document.content}"
                    for document in parent_documents
                )
                sections.append(f"<{state_label}>\n{body}\n</{state_label}>")

            parent_identity = f"{anchor.trajectory_id}:{anchor.state_index}"
            parent_id = "trajectory_parent_" + hashlib.sha256(
                parent_identity.encode("utf-8")
            ).hexdigest()[:20]
            retrieved.append(
                RetrievedParent(
                    parent_id=parent_id,
                    trajectory_id=anchor.trajectory_id,
                    anchor_state_index=anchor.state_index,
                    anchor_record_id=hit.document.record_id,
                    record_ids=tuple(
                        document.record_id for document in selected_documents
                    ),
                    state_indices=tuple(
                        key[1] for key in parent_keys if key[1] is not None
                    ),
                    content="\n".join(sections),
                    score=hit.score,
                    bm25_score=hit.bm25_score,
                    vector_score=hit.vector_score,
                    bm25_rank=hit.bm25_rank,
                    vector_rank=hit.vector_rank,
                    deterministic_score=hit.deterministic_score,
                    cross_encoder_score=hit.cross_encoder_score,
                )
            )
        return retrieved, {
            "trajectory_count": len(self.trajectory_ids),
            "document_count": len(self.documents),
            "candidate_count": len(hits),
            "parent_count": len(retrieved),
            "parent_limit": parent_limit,
            "query_seconds": duration,
            **self.retriever.last_diagnostics,
        }


class TrajectorySearchSession:
    """Per-question tool budgets and discovered-parent access control."""

    def __init__(
        self,
        index: TrajectoryMemoryIndex,
        *,
        max_searches: int = AGENTIC_MAX_SEARCHES,
        max_reads: int = AGENTIC_MAX_READS,
        evidence_token_budget: int = MEMORY_CONTEXT_TOKEN_BUDGET,
        recorder: TraceRecorder | None = None,
        run_id: str | None = None,
    ) -> None:
        self.index = index
        self.max_searches = max(1, max_searches)
        self.max_reads = max(1, max_reads)
        self.evidence_token_budget = max(1, evidence_token_budget)
        self.recorder = recorder
        self.run_id = run_id
        self.search_calls = 0
        self.read_calls = 0
        self.evidence_tokens_used = 0
        self.discovered: dict[str, RetrievedParent] = {}
        self.read_parent_ids: set[str] = set()
        self.searches: list[dict[str, Any]] = []

    def record_tool(self, tool: str, started: float, data: dict[str, Any]) -> None:
        if self.recorder:
            self.recorder.record(
                "tool.call",
                {
                    "tool": tool,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                    **data,
                },
                run_id=self.run_id,
            )

    def stats(self) -> dict[str, Any]:
        return {
            "mode": "agentic-search",
            "trajectory_count": len(self.index.trajectory_ids),
            "document_count": len(self.index.documents),
            "search_calls": self.search_calls,
            "read_calls": self.read_calls,
            "discovered_parents": len(self.discovered),
            "read_parents": len(self.read_parent_ids),
            "evidence_tokens_used": self.evidence_tokens_used,
            "evidence_token_budget": self.evidence_token_budget,
            "searches": list(self.searches),
            "query_seconds": sum(float(item["query_seconds"]) for item in self.searches),
        }


class TrajectorySearchTool:
    name = "trajectory_search"
    description = (
        "Search remembered LongMemEval trajectories using BM25, local bi-encoder vectors, "
        "RRF, and optional Cross-Encoder reranking. Returns discoverable parent ids and short "
        "previews. Reformulate the query and search again when the first evidence is insufficient."
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

    def __init__(self, session: TrajectorySearchSession) -> None:
        self.session = session

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        started = time.perf_counter()
        if self.session.search_calls >= self.session.max_searches:
            message = f"search budget exhausted ({self.session.max_searches})"
            self.session.record_tool(self.name, started, {"status": "error", "error": message})
            return ToolResult(message, is_error=True, details={"budget_exhausted": True})
        query = str(arguments["query"]).strip()
        if not query:
            message = "query must not be empty"
            self.session.record_tool(self.name, started, {"status": "error", "error": message})
            return ToolResult(message, is_error=True)
        limit = max(1, min(10, int(arguments.get("limit", 5))))
        self.session.search_calls += 1
        hits, diagnostics = self.session.index.search(
            query,
            limit=limit,
            maximum_limit=10,
        )
        for hit in hits:
            self.session.discovered[hit.parent_id] = hit
        search_record = {
            "query": query,
            "limit": limit,
            **diagnostics,
        }
        self.session.searches.append(search_record)
        payload = [
            {
                "parent_id": hit.parent_id,
                "trajectory_id": hit.trajectory_id,
                "anchor_state_index": hit.anchor_state_index,
                "state_indices": list(hit.state_indices),
                "preview": truncate_retrieval_text(hit.content, 180),
                "score": round(hit.score, 6),
            }
            for hit in hits
        ]
        self.session.record_tool(
            self.name,
            started,
            {
                "status": "success",
                "arguments": {"query": query, "limit": limit},
                "result_count": len(payload),
                "retrieval": diagnostics,
            },
        )
        if self.session.recorder:
            self.session.recorder.record(
                "memory.retrieval",
                {"query": query, "items": payload, **diagnostics},
                run_id=self.session.run_id,
            )
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            details={"result_count": len(payload), "retrieval": diagnostics},
        )


class TrajectoryReadTool:
    name = "trajectory_read"
    description = (
        "Read the full neighboring-state evidence for a parent id returned by "
        "trajectory_search. Only previously discovered ids are allowed."
    )
    input_schema = {
        "type": "object",
        "properties": {"parent_id": {"type": "string"}},
        "required": ["parent_id"],
        "additionalProperties": False,
    }

    def __init__(self, session: TrajectorySearchSession) -> None:
        self.session = session

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        started = time.perf_counter()
        parent_id = str(arguments["parent_id"]).strip()
        hit = self.session.discovered.get(parent_id)
        if hit is None:
            message = "parent_id was not returned by trajectory_search"
            self.session.record_tool(
                self.name,
                started,
                {"status": "error", "parent_id": parent_id, "error": message},
            )
            return ToolResult(message, is_error=True, details={"not_discovered": True})
        if parent_id in self.session.read_parent_ids:
            message = "parent_id was already read; use the existing evidence"
            self.session.record_tool(
                self.name,
                started,
                {"status": "error", "parent_id": parent_id, "error": message},
            )
            return ToolResult(message, is_error=True, details={"duplicate_read": True})
        if self.session.read_calls >= self.session.max_reads:
            message = f"read budget exhausted ({self.session.max_reads})"
            self.session.record_tool(self.name, started, {"status": "error", "error": message})
            return ToolResult(message, is_error=True, details={"budget_exhausted": True})

        remaining = self.session.evidence_token_budget - self.session.evidence_tokens_used
        if remaining <= 0:
            message = "evidence token budget exhausted"
            self.session.record_tool(self.name, started, {"status": "error", "error": message})
            return ToolResult(message, is_error=True, details={"budget_exhausted": True})
        header = (
            f"[Evidence parent={hit.parent_id}; trajectory={hit.trajectory_id}; "
            f"anchor_state={hit.anchor_state_index}]\n"
        )
        full_rendered = header + hit.content
        truncated = estimate_retrieval_tokens(full_rendered) > remaining
        rendered = (
            truncate_retrieval_text(full_rendered, remaining)
            if truncated
            else full_rendered
        )
        consumed = estimate_retrieval_tokens(rendered)
        self.session.read_calls += 1
        self.session.read_parent_ids.add(parent_id)
        self.session.evidence_tokens_used += consumed
        details = {
            "parent_id": parent_id,
            "evidence_tokens": consumed,
            "evidence_tokens_used": self.session.evidence_tokens_used,
            "evidence_token_budget": self.session.evidence_token_budget,
            "truncated": truncated,
        }
        self.session.record_tool(
            self.name,
            started,
            {"status": "success", **details},
        )
        return ToolResult(rendered, details=details)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def normalize_presentational_latex(value: str) -> str:
    """Remove harmless LaTeX text wrappers before deterministic judging.

    Models commonly place a textual answer inside ``\\text{}`` within the
    required ``\\boxed{}``.  The official phrase matcher otherwise treats the
    command name as part of the answer (for example, ``textResults``), causing
    semantically correct answers to fail exact deterministic evaluation.
    """

    normalized = value.strip()
    while True:
        match = _LATEX_TEXT_WRAPPER.fullmatch(normalized)
        if match is None:
            return normalized
        normalized = match.group(1).strip()


def _stable_key(seed: int, *parts: str) -> bytes:
    return hashlib.sha256(f"{seed}:".encode() + ":".join(parts).encode("utf-8")).digest()


def load_questions(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def select_questions(
    rows: list[dict[str, Any]],
    *,
    limit: int,
    seed: int,
    text_only: bool,
) -> list[SelectedQuestion]:
    active_question_types = tuple(
        question_type
        for question_type in QUESTION_TYPES
        if not (text_only and question_type == "errors-gotchas")
    )
    if limit < len(DOMAINS) * len(active_question_types):
        raise ValueError(
            f"limit must be at least {len(DOMAINS) * len(active_question_types)} "
            "to cover every domain/question-type stratum"
        )
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        domain = str(row.get("domain") or "")
        question_type = str(row.get("question_type") or "")
        if domain not in DOMAINS or question_type not in active_question_types:
            continue
        if text_only and row.get("image"):
            continue
        groups[(domain, question_type)].append(row)

    strata = [
        (domain, question_type)
        for domain in DOMAINS
        for question_type in active_question_types
    ]
    missing = [stratum for stratum in strata if not groups.get(stratum)]
    if missing:
        raise ValueError(f"missing selectable strata: {missing}")
    for stratum, members in groups.items():
        members.sort(key=lambda row: _stable_key(seed, stratum[0], stratum[1], str(row["id"])))

    quotas = {stratum: limit // len(strata) for stratum in strata}
    remainder = limit - sum(quotas.values())
    remainder_order = sorted(strata, key=lambda item: _stable_key(seed, *item))
    for stratum in remainder_order[:remainder]:
        quotas[stratum] += 1

    selected: list[SelectedQuestion] = []
    for stratum in strata:
        members = groups[stratum]
        quota = quotas[stratum]
        if len(members) < quota:
            raise ValueError(f"stratum {stratum} has {len(members)} rows, needs {quota}")
        for row in members[:quota]:
            selected.append(
                SelectedQuestion(
                    question_id=str(row["id"]),
                    domain=str(row["domain"]),
                    question_type=str(row["question_type"]),
                    question=str(row["question"]),
                    answer=str(row["answer"]),
                    eval_function=str(row["eval_function"]),
                )
            )
    selected.sort(key=lambda item: _stable_key(seed, item.question_id))
    return selected


def select_questions_by_id(
    rows: list[dict[str, Any]],
    question_ids: list[str],
    *,
    text_only: bool,
) -> list[SelectedQuestion]:
    """Select an explicit, ordered set without coupling it to seed/limit sampling."""

    requested = [value.strip() for value in question_ids if value.strip()]
    if len(requested) != len(set(requested)):
        raise ValueError("question ids must be unique")
    available: dict[str, SelectedQuestion] = {}
    for row in rows:
        question_id = str(row.get("id") or "")
        domain = str(row.get("domain") or "")
        question_type = str(row.get("question_type") or "")
        if domain not in DOMAINS or question_type not in QUESTION_TYPES:
            continue
        if text_only and (question_type == "errors-gotchas" or row.get("image")):
            continue
        available[question_id] = SelectedQuestion(
            question_id=question_id,
            domain=domain,
            question_type=question_type,
            question=str(row["question"]),
            answer=str(row["answer"]),
            eval_function=str(row["eval_function"]),
        )
    missing = [question_id for question_id in requested if question_id not in available]
    if missing:
        raise ValueError("unknown or ineligible question ids: " + ", ".join(missing))
    return [available[question_id] for question_id in requested]


def _load_id_file(path: str | None) -> list[str]:
    if not path:
        return []
    values: list[str] = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            values.append(value)
    return values


def load_haystacks(path: Path, selected: list[SelectedQuestion]) -> dict[str, list[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    selected_ids = {item.question_id for item in selected}
    missing = selected_ids - set(raw)
    if missing:
        raise ValueError(f"selected questions missing from haystack: {sorted(missing)}")
    return {question_id: list(raw[question_id]) for question_id in selected_ids}


def build_trajectory_offsets(
    path: Path,
    required_ids: set[str],
    *,
    cache_path: Path,
) -> dict[str, TrajectoryOffset]:
    signature = {"path": str(path.resolve()), "size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("source") == signature:
            offsets = {
                key: TrajectoryOffset(offset=int(value["offset"]), length=int(value["length"]))
                for key, value in cached.get("offsets", {}).items()
                if key in required_ids
            }
            if set(offsets) == required_ids:
                return offsets

    offsets: dict[str, TrajectoryOffset] = {}
    with path.open("rb") as handle:
        while True:
            offset = handle.tell()
            line = handle.readline()
            if not line:
                break
            match = _TRAJECTORY_ID.match(line[:256])
            if not match:
                continue
            trajectory_id = match.group(1).decode("utf-8")
            if trajectory_id in required_ids:
                offsets[trajectory_id] = TrajectoryOffset(offset=offset, length=len(line))
                if len(offsets) == len(required_ids):
                    break
    missing = required_ids - set(offsets)
    if missing:
        raise ValueError(f"missing trajectories: {sorted(missing)[:20]}")
    _write_json(
        cache_path,
        {
            "source": signature,
            "offsets": {key: asdict(value) for key, value in offsets.items()},
        },
    )
    return offsets


def read_trajectory(
    handle: BinaryIO,
    trajectory_id: str,
    offsets: dict[str, TrajectoryOffset],
) -> dict[str, Any]:
    location = offsets[trajectory_id]
    handle.seek(location.offset)
    raw = handle.read(location.length)
    value = json.loads(raw)
    if value.get("id") != trajectory_id:
        raise ValueError(f"trajectory offset mismatch for {trajectory_id}")
    return value


def _chunks(text: str, limit: int) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return []
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in normalized.splitlines():
        if len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current, size = [], 0
            chunks.extend(line[start : start + limit] for start in range(0, len(line), limit))
            continue
        line_size = len(line) + 1
        if current and size + line_size > limit:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += line_size
    if current:
        chunks.append("\n".join(current))
    return [chunk for chunk in chunks if chunk.strip()]


def trajectory_documents(
    trajectory: dict[str, Any],
    *,
    chunk_chars: int,
) -> tuple[list[MemoryDocument], dict[str, TrajectoryChunkMetadata]]:
    trajectory_id = str(trajectory["id"])
    goal = str(trajectory.get("goal") or "")
    outcome = str(trajectory.get("outcome") or "")
    documents: list[MemoryDocument] = []
    metadata: dict[str, TrajectoryChunkMetadata] = {}
    seen: set[str] = set()

    def add(field: str, text: str, state_index: int | None) -> None:
        for chunk_index, chunk in enumerate(_chunks(text, chunk_chars)):
            normalized = " ".join(chunk.casefold().split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            suffix = "root" if state_index is None else f"state-{state_index}"
            record_id = f"{trajectory_id}:{suffix}:{field}:{chunk_index}"
            documents.append(
                MemoryDocument(
                    record_id=record_id,
                    category="archive",
                    subject=goal[:1_000],
                    content=chunk,
                )
            )
            metadata[record_id] = TrajectoryChunkMetadata(
                trajectory_id=trajectory_id,
                state_index=state_index,
                source_field=field,
                chunk_index=chunk_index,
            )

    add(
        "trajectory",
        f"Environment: {trajectory.get('environment', '')}\nGoal: {goal}\nOutcome: {outcome}",
        None,
    )
    for position, state in enumerate(trajectory.get("states") or []):
        if not isinstance(state, dict):
            continue
        state_index = int(state.get("state_index", position))
        compact_fields = []
        for field in ("url", "action", "thought"):
            value = state.get(field)
            if value is not None and str(value).strip():
                compact_fields.append(f"{field.title()}: {value}")
        add("state", "\n".join(compact_fields), state_index)
        tree = state.get("accessibility_tree")
        if isinstance(tree, str):
            add("accessibility_tree", tree, state_index)
    return documents, metadata


def retrieve_case(
    *,
    question: SelectedQuestion,
    trajectory_ids: list[str],
    trajectory_path: Path,
    offsets: dict[str, TrajectoryOffset],
    top_k: int,
    chunk_chars: int,
    retriever: HybridMemoryRetriever | None = None,
) -> tuple[list[RetrievedParent], dict[str, Any]]:
    index = TrajectoryMemoryIndex(
        trajectory_ids=trajectory_ids,
        trajectory_path=trajectory_path,
        offsets=offsets,
        chunk_chars=chunk_chars,
        retriever=retriever,
    )
    return index.search(question.question, limit=top_k)


def render_memory_context(
    hits: list[RetrievedParent],
    max_tokens: int = MEMORY_CONTEXT_TOKEN_BUDGET,
) -> str:
    lines = ["### Memory context"]
    max_tokens = max(1, max_tokens)
    remaining_tokens = max(0, max_tokens - estimate_retrieval_tokens(lines[0]))
    distributable_tokens = max(0, remaining_tokens - (2 * len(hits)))
    per_parent_tokens = max(1, distributable_tokens // max(1, len(hits)))
    for rank, hit in enumerate(hits, start=1):
        state = (
            "root"
            if hit.anchor_state_index is None
            else f"anchor-state={hit.anchor_state_index}"
        )
        header = (
            f"\n[Evidence parent {rank}; parent={hit.parent_id}; "
            f"trajectory={hit.trajectory_id}; {state}]\n"
        )
        marker = "\n…[parent context truncated by 15k token budget]"
        content_limit = max(
            0,
            per_parent_tokens - estimate_retrieval_tokens(header + marker),
        )
        content = hit.content
        if estimate_retrieval_tokens(content) > content_limit:
            content = truncate_retrieval_text(content, content_limit) + marker
        block = f"{header}{content}"
        candidate = "\n".join([*lines, block])
        if estimate_retrieval_tokens(candidate) > max_tokens:
            break
        lines.append(block)
    if len(lines) == 1:
        lines.append("\n(empty)")
    return "\n".join(lines)


async def _complete(
    client: Any,
    profile: ModelProfile,
    messages: list[ChatMessage],
    *,
    purpose: str,
    max_output_tokens: int,
) -> tuple[str, str, TokenUsage]:
    output = ""
    error = ""
    usage = TokenUsage()
    request = ModelRequest(
        profile=replace(
            profile,
            max_output_tokens=min(max_output_tokens, profile.max_output_tokens),
            supports_tools=False,
        ),
        messages=messages,
        temperature=0,
        metadata={"purpose": purpose},
    )
    async for event in client.stream(request):
        if event.type == "completed" and event.reply is not None:
            output = event.reply.content
            error = event.reply.error or ""
            usage = event.reply.usage
        elif event.type == "error":
            error = event.error or "model error"
    return output.strip(), error, usage


def _add_usage(total: TokenUsage, value: TokenUsage | None) -> None:
    if value is None:
        return
    total.input_tokens += value.input_tokens
    total.output_tokens += value.output_tokens
    total.cached_tokens += value.cached_tokens


async def run_agentic_search_case(
    *,
    question: SelectedQuestion,
    index: TrajectoryMemoryIndex,
    client: Any,
    profile: ModelProfile,
    recorder: TraceRecorder,
    run_id: str,
    max_searches: int,
    max_reads: int,
    evidence_token_budget: int,
    max_agent_turns: int,
) -> tuple[str, str, TokenUsage, list[RetrievedParent], dict[str, Any]]:
    session = TrajectorySearchSession(
        index,
        max_searches=max_searches,
        max_reads=max_reads,
        evidence_token_budget=evidence_token_budget,
        recorder=recorder,
        run_id=run_id,
    )
    executor = ToolExecutor(timeout_seconds=120, max_output_chars=100_000)
    executor.register(TrajectorySearchTool(session))
    executor.register(TrajectoryReadTool(session))
    system_prompt = (
        DOMAIN_SYSTEM_PROMPTS[question.domain]
        + "\n\nYou have no trajectory evidence in the initial prompt. Always call "
        "trajectory_search before answering. Search by the most discriminative entities, "
        "UI labels, actions, states, or outcomes in the question. If the first search does "
        "not establish the answer, reformulate the query from another angle. Use "
        "trajectory_read on the most relevant discovered parent ids; search previews are "
        "navigation hints, not complete evidence. Base the answer only on evidence returned "
        "by these tools. Keep the final answer in the benchmark-required \\boxed{} format."
    )
    loop = AgentLoop(
        client,
        replace(profile, supports_tools=True),
        executor,
        system_prompt=system_prompt,
        max_turns=max(2, max_agent_turns),
    )
    response = ""
    error = ""
    usage = TokenUsage()
    async for event in loop.run(question.question):
        if (
            event.type == "message_added"
            and event.message is not None
            and event.message.role == "assistant"
        ):
            _add_usage(usage, event.usage)
        elif event.type == "run_finished" and event.message is not None:
            response = event.message.content.strip()
            if event.is_error:
                error = str((event.details or {}).get("error") or "agent run failed")
        elif event.type == "error":
            error = event.text or "agent run failed"

    retrieved = list(session.discovered.values())
    stats = session.stats()
    stats["max_agent_turns"] = max(2, max_agent_turns)
    return response, error, usage, retrieved, stats


def load_official_metrics(repo: Path) -> ModuleType:
    path = repo / "evaluation" / "qa_eval_metrics.py"
    spec = importlib.util.spec_from_file_location("longmemeval_v2_official_metrics", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load official metrics from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def score_case(
    metrics: ModuleType,
    client: Any,
    profile: ModelProfile,
    question: SelectedQuestion,
    response: str,
    parsed_answer: str,
) -> tuple[bool, str, str]:
    eval_name = metrics.eval_name(question.eval_function)
    if eval_name not in LLM_EVAL_FUNCTIONS:
        raw = metrics.eval_from_spec(question.eval_function, parsed_answer, question.answer)
        return bool(metrics.score_to_bool(raw)), "official_deterministic", ""

    question_item = {"question": question.question}
    if eval_name == "llm_abstention_checker":
        judge_messages = metrics._build_abstention_judge_messages(
            question_text=question.question,
            reference_answer=question.answer,
            model_full_response=response,
            model_final_answer=parsed_answer,
        )
    else:
        judge_messages = metrics._build_gotchas_judge_messages(
            question_text=question.question,
            reference_answer=question.answer,
            model_full_response=response,
            model_final_answer=parsed_answer,
        )
    del question_item
    judge_text, error, _usage = await _complete(
        client,
        profile,
        [ChatMessage(role=item["role"], content=item["content"]) for item in judge_messages],
        purpose=f"longmemeval_v2_{eval_name}",
        max_output_tokens=256,
    )
    if error:
        return False, "official_llm_judge", error
    try:
        label, _reason = metrics._parse_llm_binary_judgement(judge_text)
    except ValueError as exc:
        return False, "official_llm_judge", str(exc)
    return label == 1, "official_llm_judge", ""


async def run(args: argparse.Namespace) -> int:
    official_repo = Path(args.official_repo).resolve()
    data_root = Path(args.data_root).resolve()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    questions_path = data_root / "questions.jsonl"
    haystack_path = data_root / "haystacks" / "lme_v2_small.json"
    trajectory_path = data_root / "trajectories.jsonl"
    for required in (questions_path, haystack_path, trajectory_path):
        if not required.exists():
            raise FileNotFoundError(required)

    rows = load_questions(questions_path)
    explicit_ids = [*args.question_id, *_load_id_file(args.question_id_file)]
    if explicit_ids:
        selected = select_questions_by_id(rows, explicit_ids, text_only=args.text_only)
    else:
        selected = select_questions(
            rows,
            limit=args.limit,
            seed=args.seed,
            text_only=args.text_only,
        )
    haystacks = load_haystacks(haystack_path, selected)
    required_trajectories = {
        trajectory_id
        for question in selected
        for trajectory_id in haystacks[question.question_id]
    }
    offsets = build_trajectory_offsets(
        trajectory_path,
        required_trajectories,
        cache_path=output_dir / "trajectory-offsets.json",
    )
    _write_json(
        output_dir / "selection.json",
        {
            "benchmark": "LongMemEval-V2-Small",
            "seed": args.seed,
            "text_only": args.text_only,
            "questions": [asdict(item) for item in selected],
            "unique_trajectories": len(required_trajectories),
        },
    )

    file_values = read_env_file(args.env_file) if args.env_file else {}
    environment = merged_environment(file_values)
    settings = load_llm_settings(
        provider=args.provider or file_values.get("MINICLAW_PROVIDER"),
        model_id=args.model,
        base_url=args.base_url,
        environment=environment,
    )
    profile = _profile(settings, args.max_output_tokens)
    recorder = TraceRecorder(output_dir, "benchmark", "longmemeval-v2-small")
    client = TracingModelClient(create_model_client(settings), recorder, settings.provider)
    metrics = load_official_metrics(official_repo)
    retriever = create_hybrid_memory_retriever(environment)

    mode = "agentic-search" if args.agentic_search else "one-shot"
    results_path = output_dir / f"results-{mode}.json"
    results: list[dict[str, Any]] = []
    if args.resume and results_path.exists():
        results = json.loads(results_path.read_text(encoding="utf-8"))
    completed = {str(item["question_id"]) for item in results}
    started = time.perf_counter()
    for position, question in enumerate(selected, start=1):
        if question.question_id in completed:
            continue
        run_id = recorder.new_run_id()
        recorder.record(
            "benchmark.case.started",
            {
                "benchmark": "LongMemEval-V2-Small",
                "question_id": question.question_id,
                "domain": question.domain,
                "question_type": question.question_type,
                "position": position,
                "total": len(selected),
            },
            run_id=run_id,
        )
        token = set_active_run(run_id)
        case_started = time.perf_counter()
        try:
            index = TrajectoryMemoryIndex(
                trajectory_ids=haystacks[question.question_id],
                trajectory_path=trajectory_path,
                offsets=offsets,
                chunk_chars=args.chunk_chars,
                retriever=retriever,
            )
            if args.agentic_search:
                response, error, usage, hits, retrieval_stats = (
                    await run_agentic_search_case(
                        question=question,
                        index=index,
                        client=client,
                        profile=profile,
                        recorder=recorder,
                        run_id=run_id,
                        max_searches=args.max_searches,
                        max_reads=args.max_reads,
                        evidence_token_budget=args.memory_context_max_tokens,
                        max_agent_turns=args.max_agent_turns,
                    )
                )
            else:
                hits, retrieval_stats = index.search(
                    question.question,
                    limit=args.retrieval_limit,
                )
                context = render_memory_context(
                    hits,
                    max_tokens=args.memory_context_max_tokens,
                )
                recorder.record(
                    "memory.retrieval",
                    {
                        "query": question.question,
                        "items": [asdict(hit) for hit in hits],
                        **retrieval_stats,
                    },
                    run_id=run_id,
                )
                response, error, usage = await _complete(
                    client,
                    profile,
                    [
                        ChatMessage(
                            role="system",
                            content=DOMAIN_SYSTEM_PROMPTS[question.domain],
                        ),
                        ChatMessage(
                            role="user",
                            content=f"{context}\n\n### Question to answer\n{question.question}",
                        ),
                    ],
                    purpose="longmemeval_v2_answer",
                    max_output_tokens=args.max_output_tokens,
                )
            parsed_raw = metrics.extract_boxed_answer(response)
            parsed = normalize_presentational_latex(parsed_raw)
            correct, score_source, score_error = await score_case(
                metrics, client, profile, question, response, parsed
            )
            result = {
                "question_id": question.question_id,
                "mode": mode,
                "domain": question.domain,
                "question_type": question.question_type,
                "question": question.question,
                "answer_gold": question.answer,
                "eval_function": question.eval_function,
                "response_raw": response,
                "response_parsed_boxed_raw": parsed_raw,
                "response_parsed_boxed": parsed,
                "is_unknown": bool(metrics.is_unknown(parsed)),
                "correct": correct,
                "score_source": score_source,
                "error": error or score_error,
                "retrieval": [asdict(hit) for hit in hits],
                "retrieval_stats": retrieval_stats,
                "usage": asdict(usage),
                "elapsed_seconds": time.perf_counter() - case_started,
                "trace_metrics": recorder.run_metrics(run_id),
            }
        except Exception as exc:
            result = {
                "question_id": question.question_id,
                "mode": mode,
                "domain": question.domain,
                "question_type": question.question_type,
                "question": question.question,
                "answer_gold": question.answer,
                "eval_function": question.eval_function,
                "response_raw": "",
                "response_parsed_boxed": "",
                "is_unknown": False,
                "correct": False,
                "score_source": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "retrieval": [],
                "retrieval_stats": {},
                "usage": asdict(TokenUsage()),
                "elapsed_seconds": time.perf_counter() - case_started,
                "trace_metrics": recorder.run_metrics(run_id),
            }
        finally:
            reset_active_run(token)
        results.append(result)
        recorder.record("benchmark.case.completed", result, run_id=run_id)
        _write_json(results_path, results)
        print(
            json.dumps(
                {
                    "position": position,
                    "question_id": question.question_id,
                    "domain": question.domain,
                    "question_type": question.question_type,
                    "correct": result["correct"],
                    "unknown": result["is_unknown"],
                    "retrieval_seconds": result.get("retrieval_stats", {}).get("query_seconds"),
                    "error": result["error"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    grouped: dict[str, dict[str, Any]] = {}
    for key_name in ("domain", "question_type"):
        values: dict[str, Any] = {}
        for key in sorted({str(item[key_name]) for item in results}):
            members = [item for item in results if item[key_name] == key]
            values[key] = {
                "cases": len(members),
                "correct": sum(bool(item["correct"]) for item in members),
                "accuracy": sum(bool(item["correct"]) for item in members) / len(members),
                "unknown": sum(bool(item["is_unknown"]) for item in members),
            }
        grouped[key_name] = values

    successful = [item for item in results if not item["error"]]
    summary = {
        "benchmark": "LongMemEval-V2-Small",
        "mode": mode,
        "selection": "domain-question-type-stratified-random",
        "seed": args.seed,
        "text_only": args.text_only,
        "provider": settings.provider,
        "model": settings.model_id,
        "cases": len(results),
        "correct": sum(bool(item["correct"]) for item in results),
        "accuracy": sum(bool(item["correct"]) for item in results) / len(results),
        "unknown": sum(bool(item["is_unknown"]) for item in results),
        "errors": sum(bool(item["error"]) for item in results),
        "input_tokens": sum(int(item["usage"]["input_tokens"]) for item in results),
        "output_tokens": sum(int(item["usage"]["output_tokens"]) for item in results),
        "average_retrieval_seconds": (
            sum(float(item["retrieval_stats"]["query_seconds"]) for item in successful)
            / len(successful)
            if successful
            else None
        ),
        "elapsed_seconds": time.perf_counter() - started,
        **grouped,
        "results_path": str(results_path),
        "trace_path": str(recorder.path),
    }
    _write_json(output_dir / f"summary-{mode}.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run MiniClaw on LongMemEval-V2 Small")
    parser.add_argument("--official-repo", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20260903)
    parser.add_argument("--text-only", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--retrieval-limit", type=int, default=PARENT_RETRIEVAL_LIMIT)
    parser.add_argument("--chunk-chars", type=int, default=1_600)
    parser.add_argument("--max-output-tokens", type=int, default=1_024)
    parser.add_argument(
        "--agentic-search",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use AgentLoop with trajectory_search/read tools; disable for one-shot A/B",
    )
    parser.add_argument("--max-searches", type=int, default=AGENTIC_MAX_SEARCHES)
    parser.add_argument("--max-reads", type=int, default=AGENTIC_MAX_READS)
    parser.add_argument(
        "--memory-context-max-tokens",
        type=int,
        default=MEMORY_CONTEXT_TOKEN_BUDGET,
    )
    parser.add_argument("--max-agent-turns", type=int, default=AGENTIC_MAX_TURNS)
    parser.add_argument(
        "--question-id",
        action="append",
        default=[],
        help="Run these explicit question ids in the supplied order (repeatable)",
    )
    parser.add_argument(
        "--question-id-file",
        help="UTF-8 file containing one explicit question id per line",
    )
    parser.add_argument("--output", default=".aster/benchmarks/results/longmemeval-v2-small")
    parser.add_argument("--env-file")
    parser.add_argument("--provider", choices=["primary", "openai", "zxcoding", "deepseek"])
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    return parser


def main() -> None:
    raise SystemExit(asyncio.run(run(build_parser().parse_args())))


if __name__ == "__main__":
    main()
