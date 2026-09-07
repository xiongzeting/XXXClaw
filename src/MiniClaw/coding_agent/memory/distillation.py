from __future__ import annotations

from MiniClaw.agent.context import is_context_update

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from MiniClaw.cancellation import CancellationToken
from MiniClaw.llm.client import ModelClient
from MiniClaw.llm.types import ChatMessage, ModelEvent, ModelProfile, ModelRequest

from .evidence import MemoryEvidenceStore, contains_secret
from .semantic import MemoryConflictError, SemanticMemoryStore
from .quality import historical_state_claim, preference_evidence, normalized


_PATH_RE = re.compile(r"(?<![\w.-])(?:[A-Za-z]:[\\/])?[^\s:'\"<>|]+[\\/][^\s:'\"<>|]+")
_ERROR_RE = re.compile(
    r"\b(?:error|failed|failure|exception|traceback|fatal|timeout|denied|exit(?:ed)?\s+with\s+code)\b",
    re.IGNORECASE,
)
_SUCCESS_RE = re.compile(
    r"\b(?:passed|success|successful|completed|ok|exit\s*code\s*0|all tests)\b|"
    r"(?:通过|成功|完成|验证通过)",
    re.IGNORECASE,
)
_TRANSIENT_EXPERIMENT_RE = re.compile(
    r"\b(?:benchmark|debug|fixture|smoke|pilot|replay|this\s+(?:run|test|experiment)|"
    r"current\s+(?:run|test|experiment))\b|"
    r"(?:本次|本轮|当前|此次)(?:测试|运行|实验|评测|基准)|(?:调试|冒烟|回放)(?:样本|结果|运行)",
    re.IGNORECASE,
)
_MEASUREMENT_RE = re.compile(
    r"\b(?:accuracy|success\s*rate|pass\s*rate|recall|precision|latency|elapsed|"
    r"throughput|tokens?|samples?|false\s+(?:accept|reject))\b|"
    r"(?:准确率|成功率|通过率|召回率|精确率|延迟|耗时|吞吐|样本数|误通过|误拒绝)",
    re.IGNORECASE,
)
_NUMERIC_RESULT_RE = re.compile(r"(?:\b\d+\s*/\s*\d+\b|\b\d+(?:\.\d+)?\s*%|\b\d+(?:\.\d+)?\s*(?:ms|s|秒|毫秒)\b)", re.IGNORECASE)


@dataclass(slots=True, frozen=True)
class MemoryConsolidationConfig:
    enabled: bool = False
    max_input_chars: int = 24_000
    max_facts: int = 6
    min_fact_confidence: float = 0.90
    max_procedures: int = 2
    min_procedure_confidence: float = 0.95


@dataclass(slots=True)
class ConsolidationResult:
    summary: str
    facts_written: int = 0
    facts_rejected: int = 0
    conflicts: int = 0
    procedure_candidates: int = 0
    model_used: bool = False
    model_error: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _boolean(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _integer(env: Mapping[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    raw = env.get(name)
    try:
        value = default if raw is None or not raw.strip() else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    try:
        value = default if raw is None or not raw.strip() else float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return value


def load_consolidation_config(
    environment: Mapping[str, str] | None = None,
) -> MemoryConsolidationConfig:
    env = os.environ if environment is None else environment
    return MemoryConsolidationConfig(
        enabled=_boolean(env, "MINICLAW_MEMORY_CONSOLIDATION_ENABLED", False),
        max_input_chars=_integer(
            env, "MINICLAW_MEMORY_CONSOLIDATION_MAX_INPUT_CHARS", 24_000, 4_000, 100_000
        ),
        max_facts=_integer(env, "MINICLAW_MEMORY_CONSOLIDATION_MAX_FACTS", 6, 0, 20),
        min_fact_confidence=_float(
            env, "MINICLAW_MEMORY_MIN_FACT_CONFIDENCE", 0.90
        ),
        max_procedures=_integer(
            env, "MINICLAW_MEMORY_CONSOLIDATION_MAX_PROCEDURES", 2, 0, 8
        ),
        min_procedure_confidence=_float(
            env, "MINICLAW_MEMORY_MIN_PROCEDURE_CONFIDENCE", 0.95
        ),
    )


def _compact(value: str, limit: int) -> str:
    normalized = " ".join(value.split()).strip()
    if len(normalized) <= limit:
        return normalized
    head = max(1, (limit * 2) // 3)
    tail = max(1, limit - head - 5)
    return f"{normalized[:head]} ... {normalized[-tail:]}"


def _is_durable_fact(content: str, evidence: str) -> bool:
    """Reject one-run measurements while retaining reusable project knowledge."""

    combined = f"{content}\n{evidence}"
    if _TRANSIENT_EXPERIMENT_RE.search(combined):
        return False
    if _MEASUREMENT_RE.search(combined) and _NUMERIC_RESULT_RE.search(combined):
        return False
    return True


def deterministic_episode_summary(
    messages: list[ChatMessage],
    *,
    status: str,
) -> str:
    users = [message.content for message in messages if message.role == "user" and message.content]
    assistants = [
        message.content for message in messages if message.role == "assistant" and message.content and not is_context_update(message)
    ]
    goal = _compact(users[-1] if users else "No explicit user request.", 1_500)
    outcome = _compact(assistants[-1] if assistants else "No assistant outcome yet.", 2_000)
    calls: list[str] = []
    files: list[str] = []
    evidence: list[str] = []
    for message in messages:
        for call in message.tool_calls:
            arguments = json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":"))
            calls.append(f"- {call.name}: {_compact(arguments, 500)}")
            files.extend(_PATH_RE.findall(arguments))
        if message.role == "tool" and message.content:
            if _ERROR_RE.search(message.content) or _SUCCESS_RE.search(message.content):
                evidence.append(f"- {message.name or 'tool'}: {_compact(message.content, 700)}")
            files.extend(_PATH_RE.findall(message.content))
    unique_files = list(dict.fromkeys(value.rstrip(".,;)") for value in files if value))
    return (
        f"## Goal\n\n{goal}\n\n"
        f"## Status\n\n{status}\n\n"
        f"## Outcome\n\n{outcome}\n\n"
        f"## Tool Activity\n\n{chr(10).join(calls[-12:]) or '- (none)'}\n\n"
        f"## Evidence\n\n{chr(10).join(evidence[-10:]) or '- (none)'}\n\n"
        f"## Files\n\n{chr(10).join(f'- {value}' for value in unique_files[-20:]) or '- (none)'}"
    )


class MemoryConsolidator:
    """Conservatively derive auditable memories after a completed task."""

    def __init__(
        self,
        *,
        model_client: ModelClient,
        profile: ModelProfile,
        semantic: SemanticMemoryStore,
        evidence: MemoryEvidenceStore,
        workspace: Path,
        session_id: str,
        config: MemoryConsolidationConfig,
        user_scope: str = "",
        channel_scope: str = "",
    ) -> None:
        self.model_client = model_client
        self.profile = profile
        self.semantic = semantic
        self.evidence = evidence
        self.workspace = workspace
        self.session_id = session_id
        self.config = config
        self.user_scope = user_scope
        self.channel_scope = channel_scope

    async def consolidate(
        self,
        messages: list[ChatMessage],
        *,
        status: str,
        cancellation_token: CancellationToken | None = None,
    ) -> ConsolidationResult:
        deterministic = deterministic_episode_summary(messages, status=status)
        result = ConsolidationResult(summary=deterministic)
        if not self.config.enabled or status != "completed":
            return result
        transcript = self._transcript(messages)
        try:
            payload = await self._extract(transcript, cancellation_token)
            result.model_used = True
        except Exception as exc:
            result.model_error = f"{type(exc).__name__}: {exc}"
            return result

        model_summary = str(payload.get("episode_summary") or "").strip()
        if model_summary and not contains_secret(model_summary):
            result.summary = deterministic + f"\n\n## Consolidated Knowledge\n\n{model_summary}"

        normalized_transcript = " ".join(transcript.split()).casefold()
        users = [m.content for m in messages if m.role == "user"]
        factual_sources = [m.content for m in messages if (
            m.role == "user" or m.role == "tool" and m.name not in {"memory", "skill", "goal", "goal_complete"}
        ) and "miniclaw-managed-memory:" not in m.content and "<retrieved_memory>" not in m.content]
        for value in list(payload.get("facts") or [])[: self.config.max_facts]:
            if not isinstance(value, dict):
                result.facts_rejected += 1
                continue
            content = " ".join(str(value.get("content") or "").split()).strip()
            evidence_quote = " ".join(str(value.get("evidence") or "").split()).strip()
            category = str(value.get("category") or "fact").casefold()
            try:
                confidence = float(value.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            if (
                category not in {"preference", "project", "environment", "fact"}
                or confidence < self.config.min_fact_confidence
                or not content
                or not evidence_quote
                or evidence_quote.casefold() not in normalized_transcript
                or not any(normalized(evidence_quote) in normalized(source) for source in factual_sources)
                or category == "preference" and preference_evidence(users, evidence_quote) is None
                or contains_secret(content)
                or not _is_durable_fact(content, evidence_quote)
                or historical_state_claim(content)
            ):
                result.facts_rejected += 1
                continue
            if category == "preference":
                # The exact source also deduplicates translated/paraphrased
                # extractions of the same user preference across runs.
                content = preference_evidence(users, evidence_quote) or evidence_quote
            try:
                write_result = self.semantic.remember(
                    category,
                    content,
                    conflict_policy="reject",
                )
                normalized_result = write_result.casefold()
                if "kept existing" in normalized_result:
                    result.conflicts += 1
                    self.evidence.append(
                        kind="semantic_conflict",
                        content=content,
                        session_id=self.session_id,
                        source_path="session-transcript",
                        user_scope=self.user_scope,
                        channel_scope=self.channel_scope,
                        workspace_scope=str(self.workspace),
                        confidence=confidence,
                        status="inactive",
                        metadata={
                            "category": category,
                            "evidence": evidence_quote,
                            "resolution": "kept_existing",
                        },
                    )
                    continue
                if "already exists" not in normalized_result:
                    result.facts_written += 1
                self.evidence.append(
                    kind="semantic",
                    content=content,
                    session_id=self.session_id,
                    source_path="session-transcript",
                    user_scope=self.user_scope,
                    channel_scope=self.channel_scope,
                    workspace_scope=str(self.workspace),
                    confidence=confidence,
                    metadata={"category": category, "evidence": evidence_quote},
                )
            except MemoryConflictError as exc:
                result.conflicts += 1
                self.evidence.append(
                    kind="semantic_conflict", content=content, session_id=self.session_id,
                    source_path="session-transcript", confidence=confidence, status="inactive",
                    user_scope=self.user_scope, channel_scope=self.channel_scope,
                    workspace_scope=str(self.workspace),
                    metadata={"category": category, "evidence": evidence_quote,
                              "conflict_id": exc.conflict.conflict_id, "resolution": "pending"},
                )
            except ValueError:
                result.facts_rejected += 1

        for value in list(payload.get("procedures") or [])[: self.config.max_procedures]:
            if not isinstance(value, dict):
                continue
            try:
                confidence = float(value.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            evidence_quote = " ".join(str(value.get("evidence") or "").split()).strip()
            title = " ".join(str(value.get("title") or "").split()).strip()
            steps = [" ".join(str(step).split()).strip() for step in value.get("steps") or []]
            steps = [step for step in steps if step]
            if (
                confidence < self.config.min_procedure_confidence
                or not title
                or len(steps) < 2
                or not evidence_quote
                or evidence_quote.casefold() not in normalized_transcript
            ):
                continue
            content = f"{title}\n" + "\n".join(f"{index}. {step}" for index, step in enumerate(steps, 1))
            if contains_secret(content):
                continue
            stored = self.evidence.append(
                kind="procedure_candidate",
                content=content,
                session_id=self.session_id,
                source_path="session-transcript",
                user_scope=self.user_scope,
                channel_scope=self.channel_scope,
                workspace_scope=str(self.workspace),
                confidence=confidence,
                metadata={
                    "title": title,
                    "evidence": evidence_quote,
                    "promotion": "requires_review_or_repetition",
                },
            )
            if stored is not None:
                result.procedure_candidates += 1
        result.details = {
            "facts_returned": len(payload.get("facts") or []),
            "procedures_returned": len(payload.get("procedures") or []),
        }
        return result

    def _transcript(self, messages: list[ChatMessage]) -> str:
        rendered: list[str] = []
        for message in messages:
            if message.tool_calls:
                calls = [
                    {"name": call.name, "arguments": call.arguments}
                    for call in message.tool_calls
                ]
                rendered.append(f"[{message.role}] tool_calls={json.dumps(calls, ensure_ascii=False)}")
            if message.content:
                limit = 2_500 if message.role == "tool" else 4_000
                rendered.append(f"[{message.role}:{message.name or ''}] {_compact(message.content, limit)}")
        text = "\n".join(rendered)
        return text[-self.config.max_input_chars :]

    async def _extract(
        self,
        transcript: str,
        cancellation_token: CancellationToken | None,
    ) -> dict[str, Any]:
        request = ModelRequest(
            profile=ModelProfile(
                model_id=self.profile.model_id,
                context_window=self.profile.context_window,
                max_output_tokens=min(self.profile.max_output_tokens, 2_048),
                supports_tools=False,
                input_cost_per_million=self.profile.input_cost_per_million,
                output_cost_per_million=self.profile.output_cost_per_million,
                cached_input_cost_per_million=self.profile.cached_input_cost_per_million,
            ),
            messages=[
                ChatMessage(
                    role="system",
                    content=(
                        "Extract conservative long-term memory from a completed coding-agent task. "
                        "Return JSON only. Store facts only when they remain useful in a future task. "
                        "Do not store temporary outputs, secrets, guesses, one-run benchmark/debug metrics, "
                        "sample counts, pass rates, latency, token usage, or a restatement of the current task. "
                        "User preferences require an explicit USER statement; never infer them from assistant behavior. "
                        "Assistant assertions and outputs of memory/skill tools are not new factual evidence. "
                        "File existence, game features and task completion belong in dated episode summaries, not stable facts. "
                        "Compare with existing semantic memory and omit paraphrases, translations, and duplicates. "
                        "A procedure must be reusable, supported by an exact evidence quote, and include at least two steps."
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=(
                        "Return this schema: {\"episode_summary\":string,\"facts\":[{\"category\":"
                        "\"preference|project|environment|fact\",\"content\":string,\"evidence\":"
                        "string,\"confidence\":number}],\"procedures\":[{\"title\":string,"
                        "\"steps\":[string],\"evidence\":string,\"confidence\":number}]}. "
                        "The evidence value must be an exact quote from the transcript.\n\n"
                        f"<existing_semantic_memory>\n{self.semantic.read()}\n</existing_semantic_memory>\n\n"
                        f"<transcript>\n{transcript}\n</transcript>"
                    ),
                ),
            ],
            metadata={"purpose": "memory_consolidation"},
            cancellation_token=cancellation_token,
        )
        content = ""
        error = ""
        async for event in self.model_client.stream(request):
            if isinstance(event, ModelEvent) and event.type == "completed" and event.reply:
                content = event.reply.content
                error = event.reply.error or ""
        if error or not content.strip():
            raise RuntimeError(error or "memory consolidation returned no content")
        value = content.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", value, re.DOTALL | re.IGNORECASE)
        if fenced:
            value = fenced.group(1)
        else:
            start, end = value.find("{"), value.rfind("}")
            if start >= 0 and end > start:
                value = value[start : end + 1]
        payload = json.loads(value)
        if not isinstance(payload, dict):
            raise ValueError("memory consolidation JSON must be an object")
        return payload
