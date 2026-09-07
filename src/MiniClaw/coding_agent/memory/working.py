from __future__ import annotations

import json
import hashlib
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from MiniClaw.cancellation import CancellationToken
from MiniClaw.agent.context import ContextJournal, is_context_update
from MiniClaw.llm.client import ModelClient
from MiniClaw.llm.types import (
    AssistantReply,
    ChatMessage,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    ToolInvocation,
)
from MiniClaw.coding_agent.tools.base import ToolResult

from .archive import ArchiveMemoryIndex, StableFactIngestor
from .artifacts import ARTIFACT_MARKER, ContextArtifact, ContextArtifactStore, format_artifact_reference
from .config import MemoryConfig
from .read_cache import ReadSnapshotCache
from .history_budget import bound_tool_history, StableRequestView


CHECKPOINT_MARKER = "[MiniClaw Working Context Checkpoint]"
MAX_LEDGER_MESSAGES = 12
MAX_LEDGER_MESSAGE_CHARS = 1_600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def estimate_message_tokens(message: ChatMessage) -> int:
    chars = len(message.content)
    chars += len(message.name or "") + len(message.tool_call_id or "")
    for call in message.tool_calls:
        chars += len(call.name) + len(call.call_id)
        chars += len(json.dumps(call.arguments, ensure_ascii=False, sort_keys=True))
    return max(1, (chars + 3) // 4)


def estimate_context_tokens(messages: list[ChatMessage]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def _message_from_dict(payload: dict[str, Any]) -> ChatMessage:
    calls = [ToolInvocation(**call) for call in payload.get("tool_calls") or []]
    return ChatMessage(
        role=payload["role"],
        content=str(payload.get("content") or ""),
        tool_calls=calls,
        tool_call_id=payload.get("tool_call_id"),
        name=payload.get("name"),
    )


@dataclass(slots=True)
class CompactionOutcome:
    messages: list[ChatMessage]
    tokens_before: int
    estimated_tokens_after: int
    strategy: str
    details: dict[str, Any]


class WorkingContext:
    """Append-only session log plus the bounded context presented to the model."""

    def __init__(
        self,
        *,
        path: Path,
        workspace: Path,
        session_id: str,
        config: MemoryConfig,
        model_client: ModelClient,
        profile: ModelProfile,
        archive_index: ArchiveMemoryIndex | None = None,
        stable_fact_ingestor: StableFactIngestor | None = None,
    ) -> None:
        self.path = path
        self.session_id = session_id
        self.config = config
        self.model_client = model_client
        self.profile = profile
        self.archive_index = archive_index
        self.stable_fact_ingestor = stable_fact_ingestor
        self.artifacts = ContextArtifactStore(workspace, session_id, config.artifact_preview_chars)
        self._active_ids: list[str | None] = []
        self._previous_summary = ""
        self.last_compaction_decision: dict[str, Any] = {}
        self._deferred_prefix: str | None = None
        self._failed_call_ids: set[str] = set()
        self.read_snapshots = ReadSnapshotCache(workspace, min(8192, max(1024, config.target_tokens * 2)))
        self.last_projection: dict[str, Any] = {}
        self._messages_after_compaction: int | None = None
        self.request_view = StableRequestView(path.with_name(path.stem + '.request-view.json'),
            {'protocol': 1, 'target_tokens': config.target_tokens, 'artifact_threshold_bytes': config.artifact_threshold_bytes})

    def _append_event(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**payload, "timestamp": payload.get("timestamp") or _now()}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def append_message(self, message: ChatMessage) -> str:
        if self._messages_after_compaction is not None and not is_context_update(message):
            self._messages_after_compaction += 1
        message_id = uuid.uuid4().hex
        self._append_event(
            {
                "type": "message",
                "id": message_id,
                "message": asdict(message),
            }
        )
        self._active_ids.append(message_id)
        return message_id

    def load(self) -> list[ChatMessage]:
        if not self.path.exists():
            self._active_ids = []
            return []

        records: list[tuple[str, ChatMessage]] = []
        latest_compaction: dict[str, Any] | None = None
        records_at_compaction = 0
        for line_index, raw_line in enumerate(
            self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        ):
            try:
                payload = json.loads(raw_line)
                if payload.get("type") == "read_snapshot":
                    self.read_snapshots.restore(payload["snapshot"])
                    continue
                if payload.get("type") == "tool_status" and payload.get("failed"):
                    self._failed_call_ids.add(str(payload.get("call_id") or ""))
                    continue
                if payload.get("type") == "compaction":
                    latest_compaction = payload
                    records_at_compaction = len(records)
                    continue
                if payload.get("type") == "message":
                    message_payload = payload["message"]
                    message_id = str(payload.get("id") or uuid.uuid4().hex)
                elif "role" in payload:  # First-version transcript compatibility.
                    message_payload = payload
                    digest = hashlib.sha256(f"{line_index}:{raw_line}".encode("utf-8")).hexdigest()
                    message_id = f"legacy-{digest[:24]}"
                else:
                    continue
                records.append((message_id, _message_from_dict(message_payload)))
            except (json.JSONDecodeError, TypeError, KeyError, ValueError):
                continue

        start = 0
        messages: list[ChatMessage] = []
        ids: list[str | None] = []
        if latest_compaction:
            self._messages_after_compaction = sum(not is_context_update(m) for _,m in records[records_at_compaction:])
            first_kept = latest_compaction.get("first_kept_message_id")
            for index, (message_id, _) in enumerate(records):
                if message_id == first_kept:
                    start = index
                    break
            self._previous_summary = str(latest_compaction.get("summary") or "")
            if self._previous_summary:
                messages.append(ChatMessage(role="assistant", content=self._checkpoint_text(self._previous_summary)))
                ids.append(None)

        for message_id, message in records[start:]:
            messages.append(message)
            ids.append(message_id)
        self._active_ids = ids
        if records:
            self._append_event(
                {
                    "type": "recovery",
                    "compaction_id": latest_compaction.get("id") if latest_compaction else None,
                    "restored_messages": len(messages),
                }
            )
        return messages

    def consolidation_state(self) -> tuple[int, str, str]:
        """Read the last incremental consolidation cursor from the append-only log."""
        if not self.path.exists():
            return 0, "", ""
        latest: dict[str, Any] | None = None
        for raw in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if payload.get("type") == "consolidation_cursor":
                latest = payload
        if not latest:
            return 0, "", ""
        return int(latest.get("cursor") or 0), str(latest.get("digest") or ""), str(latest.get("summary") or "")

    def record_consolidation_state(self, cursor: int, digest: str, summary: str) -> None:
        self._append_event({"type": "consolidation_cursor", "cursor": cursor, "digest": digest, "summary": summary})

    async def artifactize_live_result(
        self,
        call: ToolInvocation,
        result: ToolResult,
    ) -> ToolResult:
        snapshot = self.read_snapshots.observe(call, result)
        if snapshot is not None:
            self._append_event({"type": "read_snapshot", "snapshot": snapshot})
        if (
            self.config.strategy == "legacy-summary-recent"
            or not self.config.progressive_enabled
            or result.is_error
            or ARTIFACT_MARKER in result.content
            or call.name == "read"
        ):
            if result.is_error:
                self._failed_call_ids.add(call.call_id)
                self._append_event({"type": "tool_status", "call_id": call.call_id, "failed": True})
            return result
        if len(result.content.encode("utf-8")) < self.config.artifact_threshold_bytes:
            return result
        artifact = self.artifacts.save(call.call_id, call.name, result.content, "txt")
        details = {**result.details, "context_artifact": artifact.to_dict()}
        return ToolResult(
            content=format_artifact_reference(artifact, include_preview=True),
            is_error=result.is_error,
            details=details,
        )

    def transform_request_context(self, messages: list[ChatMessage], *, persist: bool = True,
                                  include_snapshots: bool = True) -> list[ChatMessage]:
        """Return a bounded copy for model requests, preserving raw history/audit."""
        if self.config.strategy == "legacy-summary-recent" or not self.config.progressive_enabled:
            return messages
        messages = ContextJournal.project(messages)
        stable, prefix_count = self.request_view.prefix(messages)
        transformed = [*stable, *[self._copy_message(message) for message in messages[prefix_count:]]]
        for message in transformed:
            if message.content.startswith(CHECKPOINT_MARKER):
                message.role = 'assistant'
        calls: dict[str, ToolInvocation] = {}
        for message in transformed:
            if message.role == "assistant":
                calls.update({call.call_id: call for call in message.tool_calls})
        completed = {message.tool_call_id for message in transformed if message.role == "tool" and message.tool_call_id in calls}
        completed -= self._failed_call_ids
        threshold = min(self.config.artifact_threshold_bytes, 8_192)
        store = self.artifacts.save if persist else self.artifacts.prepare
        # Keep the just-returned read batch consumable. It becomes ordinary history
        # only after another assistant/user message; explicit rereads always work.
        fresh = set()
        for message in reversed(transformed):
            if is_context_update(message):
                continue
            if message.role != "tool":
                break
            fresh.add(message.tool_call_id)
        latest_read = {}
        duplicated = {}
        for message in reversed(transformed):
            if message.role != "tool" or message.tool_call_id not in completed:
                continue
            call = calls[message.tool_call_id]
            if call.name != "read":
                continue
            key = (json.dumps(call.arguments, sort_keys=True, ensure_ascii=False),
                   hashlib.sha256(message.content.encode("utf-8")).hexdigest())
            if key in latest_read and len(message.content) > 256:
                duplicated[call.call_id] = latest_read[key]
            else:
                latest_read[key] = call.call_id

        def visit(value: Any, call: ToolInvocation, key: str) -> Any:
            call_size = len(json.dumps(calls[call.call_id].arguments, ensure_ascii=False).encode("utf-8"))
            field_threshold = min(threshold, 2048) if call_size >= threshold else threshold
            if isinstance(value, str) and len(value.encode("utf-8")) >= field_threshold:
                artifact = store(call.call_id, f"{call.name}-{key or 'argument'}", value, "txt")
                reference = format_artifact_reference(replace(artifact, preview=artifact.preview[:800]), include_preview=True)
                return reference if len(reference.encode("utf-8")) < len(value.encode("utf-8")) else value
            if isinstance(value, dict):
                return {k: visit(v, call, f"{key}.{k}" if key else str(k)) for k, v in value.items()}
            if isinstance(value, list):
                return [visit(v, call, f"{key}[{i}]") for i, v in enumerate(value)]
            return value

        for message in transformed[prefix_count:]:
            if message.role == "assistant":
                for call in message.tool_calls:
                    if call.call_id in completed:
                        call.arguments = visit(call.arguments, call, "")
            elif message.role == "tool" and message.tool_call_id in completed and not message.content.startswith("[MiniClaw context artifact]"):
                if message.tool_call_id in duplicated:
                    message.content = ("[Historical duplicate read; identical arguments and returned content. "
                                       f"Full result retained at tool call {duplicated[message.tool_call_id]}.]")
                    continue
                if not message.content or (message.tool_call_id in fresh and calls[message.tool_call_id].name == "read"):
                    continue
                if len(message.content.encode("utf-8")) >= threshold:
                    call = calls[message.tool_call_id]
                    artifact = store(call.call_id, call.name, message.content, "txt")
                    reference = format_artifact_reference(replace(artifact, preview=artifact.preview[:800]), include_preview=True)
                    if len(reference.encode("utf-8")) < len(message.content.encode("utf-8")):
                        message.content = reference
        budget_details = bound_tool_history(
            transformed, messages, completed, fresh, store,
            min(16_384, max(4_096, self.config.target_tokens * 2)),
            high_water=(2 * min(16_384, max(4_096, self.config.target_tokens * 2))) if prefix_count else None,
        )
        if include_snapshots:
            before_replay_tokens = estimate_context_tokens(transformed)
            raw_tokens = estimate_context_tokens(messages)
            if persist:
                self.request_view.save(messages, transformed)
            transformed, snapshot_details = self.read_snapshots.project(transformed)
            self.last_projection = {**snapshot_details, **budget_details, "duplicate_read_results_removed": len(duplicated),
                                    'stable_history_messages': prefix_count,
                                    "raw_history_tokens": raw_tokens,
                                    "history_before_replay_tokens": before_replay_tokens,
                                    "history_projection_saved_tokens": raw_tokens - before_replay_tokens,
                                    "read_replay_added_tokens": estimate_context_tokens(transformed) - before_replay_tokens,
                                    "history_net_added_tokens": estimate_context_tokens(transformed) - raw_tokens,
                                    "request_history_tokens": estimate_context_tokens(transformed)}
        return transformed

    async def maybe_compact(
        self,
        messages: list[ChatMessage],
        provider_input_tokens: int = 0,
        cancellation_token: CancellationToken | None = None,
    ) -> CompactionOutcome | None:
        self.last_compaction_decision = {"reason": "below_trigger"}
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled(stage="compaction_start")
        if not self.config.enabled or len(messages) < 2:
            return None
        tokens_before = provider_input_tokens or estimate_context_tokens(messages)
        if (self._messages_after_compaction is not None and self._messages_after_compaction < 8
                and tokens_before < self.profile.context_window - self.config.reserve_tokens
                and self.config.strategy != 'legacy-summary-recent'):
            self.last_compaction_decision = {'reason':'minimum_compaction_interval',
                                            'new_messages':self._messages_after_compaction}
            return None
        if self.config.strategy == "legacy-summary-recent":
            if tokens_before <= self.config.hard_trigger_tokens:
                return None
            return await self._compact(
                messages,
                tokens_before=tokens_before,
                reason="hard",
                cancellation_token=cancellation_token,
            )
        if tokens_before <= self.config.soft_trigger_tokens:
            return None
        reason = "hard" if tokens_before > self.config.hard_trigger_tokens else "soft"
        return await self._compact(
            messages,
            tokens_before=tokens_before,
            reason=reason,
            cancellation_token=cancellation_token,
        )

    async def _compact(
        self,
        messages: list[ChatMessage],
        *,
        tokens_before: int,
        reason: str,
        cancellation_token: CancellationToken | None = None,
    ) -> CompactionOutcome | None:
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled(stage="compaction_prepare")
        first_kept = self._find_cut_point(messages)
        if first_kept <= 0:
            self.last_compaction_decision = {"reason": "no_safe_cut"}
            return None
        archived = [self._copy_message(message) for message in messages[:first_kept]
                    if message.role != "system" and not message.content.startswith(CHECKPOINT_MARKER)]
        retained = messages[first_kept:]
        if not archived or not retained:
            self.last_compaction_decision = {"reason": "no_closed_history"}
            return None

        legacy = self.config.strategy == "legacy-summary-recent"
        first_kept_id = next(
            (value for value in self._active_ids[first_kept:] if value), None
        )
        if not first_kept_id:
            self.last_compaction_decision = {"reason": "no_durable_cut"}
            return None
        # Only the actual request history is reclaimable. Raw logs may already
        # be references in model input, and fixed tool/system overhead is not history.
        projected = messages if legacy else self.transform_request_context(messages, persist=False, include_snapshots=False)
        projected_retained = retained if legacy else self.transform_request_context(retained, persist=False, include_snapshots=False)
        local_before = estimate_context_tokens(projected)
        retained_tokens = estimate_context_tokens(projected_retained)
        if (not legacy and local_before <= self.config.target_tokens
                and tokens_before < self.profile.context_window - self.config.reserve_tokens):
            self.last_compaction_decision = {"reason": "history_within_target",
                                            "request_history_tokens": local_before,
                                            "provider_input_tokens": tokens_before}
            return None
        minimum_saving = max(1, min(256, local_before // 20))
        prefix_key = hashlib.sha256(
            json.dumps(
                [self._previous_summary, [asdict(m) for m in archived]],
                ensure_ascii=False, sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if not legacy and prefix_key == self._deferred_prefix:
            self.last_compaction_decision = {"reason": "unchanged_low_gain_prefix"}
            return None
        semantic_tokens = sum(
            estimate_message_tokens(message) for message in archived
            if not is_context_update(message) and (message.role != "tool" or message.tool_call_id in self._failed_call_ids)
        )
        # Soft deferral is a decision, not a compaction attempt with durable side effects.
        if not legacy and reason == "soft" and (
            semantic_tokens + len(self._previous_summary) // 4
            > self.config.deterministic_semantic_tokens
        ):
            self.last_compaction_decision = {"reason": "soft_requires_model"}
            return None
        if not legacy and local_before - retained_tokens <= minimum_saving:
            self.last_compaction_decision = {"reason": "insufficient_reclaimable_context"}
            self._deferred_prefix = prefix_key
            return None
        pending_artifacts: list[tuple[ContextArtifact, str]] = []
        artifacts = [] if legacy else self._artifactize_old_results(archived, pending_artifacts)
        archive = None
        if not legacy:
            archive_text = "".join(
                json.dumps(asdict(message), ensure_ascii=False, default=str) + "\n"
                for message in archived
            )
            archive = self.artifacts.prepare(
                f"compaction-{uuid.uuid4().hex[:12]}",
                "context-transcript",
                archive_text,
                "jsonl",
            )
            pending_artifacts.append((archive, archive_text))
        # The latest retained request is already sent verbatim. Repeating it in every
        # checkpoint amplified large user inputs (including irrelevant pasted logs).
        current_request = (
            "See the latest retained user message below; it remains verbatim."
            if any(message.role == "user" for message in retained)
            else self._latest_user_request(messages)
        )
        if not legacy and len(current_request) > MAX_LEDGER_MESSAGE_CHARS:
            current_request = (
                "The current request is included in the archived conversation. "
                "Preserve its goals and constraints in the checkpoint; recover exact "
                "wording from the archived source when needed."
            )
        read_files, modified_files = self._file_operations(archived)
        deterministic = (
            self._legacy_deterministic_checkpoint(current_request, archived)
            if legacy
            else self._deterministic_checkpoint(
                current_request=current_request,
                archived=archived,
                archive=archive,
                artifacts=artifacts,
                read_files=read_files,
                modified_files=modified_files,
            )
        )
        estimated_after = retained_tokens + max(1, len(deterministic) // 4)
        use_model = legacy or (
            semantic_tokens + len(self._previous_summary) // 4 > self.config.deterministic_semantic_tokens
            or estimated_after > self.config.target_tokens
        )
        if use_model and reason == "soft":
            self.last_compaction_decision = {"reason": "soft_requires_model"}
            return None
        strategy = "deterministic-archive"
        summary = deterministic
        model_summary_error: str | None = None
        if use_model:
            try:
                summary = await self._model_checkpoint(
                    current_request,
                    archived,
                    archive,
                    read_files,
                    modified_files,
                    cancellation_token,
                    include_archive_reference=not legacy,
                    summary_budget_tokens=max(128, self.config.target_tokens - retained_tokens - 200),
                )
                strategy = "model-summary"
            except RuntimeError as exc:
                # Hard compaction must remain recoverable when a provider spends its
                # whole output budget on reasoning or returns an empty summary. The
                # append-only transcript is still durable. Fall back to a deterministic
                # checkpoint; persist its archive only if the result is accepted.
                summary = deterministic
                strategy = "deterministic-fallback"
                model_summary_error = str(exc)
            estimated_after = retained_tokens + max(1, len(summary) // 4)

        # Compare like-for-like local history estimates. Provider input also includes
        # tools/system/retrieval; shrinking history cannot reclaim that fixed overhead.
        estimated_after = estimate_context_tokens([
            ChatMessage(role="assistant", content=self._checkpoint_text(summary)), *projected_retained
        ])
        if not legacy and local_before - estimated_after < minimum_saving:
            self.last_compaction_decision = {
                "reason": "insufficient_token_savings",
                "estimated_history_before": local_before,
                "estimated_history_after": estimated_after,
                "minimum_saving_tokens": minimum_saving,
            }
            self._deferred_prefix = prefix_key
            return None

        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled(stage="compaction_commit")

        for artifact, content in pending_artifacts:
            self.artifacts.persist(artifact, content)
        archive_indexed_chunks = 0
        archive_index_error: str | None = None
        if archive is not None and self.archive_index is not None:
            try:
                archive_indexed_chunks = self.archive_index.add_messages(
                    self.session_id, archive.path, archived,
                )
            except Exception as exc:
                archive_index_error = f"{type(exc).__name__}: {exc}"
        stable_fact_stats: dict[str, int] | None = None
        if not legacy and self.stable_fact_ingestor is not None:
            stable_fact_stats = self.stable_fact_ingestor.ingest([m for m in archived if not is_context_update(m)])

        compaction_id = uuid.uuid4().hex
        details = {
            "version": 1,
            "reason": reason,
            "strategy": strategy,
            "compaction_strategy": self.config.strategy,
            "layers_applied": (
                [
                    "recent-original",
                    *(["model-summary"] if strategy == "model-summary" else []),
                    *(
                        ["deterministic-summary-fallback"]
                        if strategy == "deterministic-fallback"
                        else []
                    ),
                ]
                if legacy
                else [
                    "tool-result-artifact",
                    "old-result-reference",
                    "closed-history-archive",
                    "recent-original",
                    *(["model-summary"] if strategy == "model-summary" else []),
                    *(
                        ["deterministic-summary-fallback"]
                        if strategy == "deterministic-fallback"
                        else []
                    ),
                ]
            ),
            "archive": archive.to_dict() if archive is not None else None,
            "archive_indexed_chunks": archive_indexed_chunks,
            "tool_artifacts": [artifact.to_dict() for artifact in artifacts],
            "semantic_tokens": semantic_tokens,
            "estimated_tokens_after": estimated_after,
            "estimated_history_tokens_before": local_before,
            "estimated_history_tokens_saved": local_before - estimated_after,
            "minimum_saving_tokens": minimum_saving,
            "transcript_retained": True,
            **(
                {"archive_index_error": archive_index_error}
                if archive_index_error is not None
                else {}
            ),
            **(
                {"stable_fact_ingest": stable_fact_stats}
                if stable_fact_stats is not None
                else {}
            ),
            **(
                {"model_summary_error": model_summary_error}
                if model_summary_error is not None
                else {}
            ),
        }
        self._append_event(
            {
                "type": "compaction",
                "id": compaction_id,
                "summary": summary,
                "first_kept_message_id": first_kept_id,
                "tokens_before": tokens_before,
                "details": details,
            }
        )
        self._previous_summary = summary
        self._messages_after_compaction = 0
        self._deferred_prefix = None
        self.last_compaction_decision = {"reason": "committed", "strategy": strategy}
        kept_ids = self._active_ids[first_kept:]
        compacted_messages = [ChatMessage(role="assistant", content=self._checkpoint_text(summary)), *retained]
        self._active_ids = [None, *kept_ids]
        return CompactionOutcome(
            messages=compacted_messages,
            tokens_before=tokens_before,
            estimated_tokens_after=estimated_after,
            strategy=strategy,
            details=details,
        )

    def _find_cut_point(self, messages: list[ChatMessage]) -> int:
        accumulated = 0
        approximate = len(messages) - 1
        for index in range(len(messages) - 1, -1, -1):
            accumulated += estimate_message_tokens(messages[index])
            approximate = index
            if accumulated >= self.config.keep_recent_tokens:
                break
        # Prefer complete turns. This is stricter than merely avoiding a tool-result cut.
        for index in range(approximate, len(messages)):
            if messages[index].role == "user":
                return index
        # A single oversized turn may be split only before an assistant message. Its following
        # tool results stay on the retained side, so call/result pairs cannot be separated.
        for index in range(approximate, len(messages)):
            if messages[index].role == "assistant" and not is_context_update(messages[index]):
                return index
        return 0

    def _artifactize_old_results(
        self, archived: list[ChatMessage],
        pending: list[tuple[ContextArtifact, str]],
    ) -> list[ContextArtifact]:
        artifacts: list[ContextArtifact] = []
        for message in archived:
            if (message.role != "tool" or not message.content or ARTIFACT_MARKER in message.content
                    or message.tool_call_id in self._failed_call_ids):
                continue
            artifact = self.artifacts.prepare(
                message.tool_call_id or uuid.uuid4().hex,
                message.name or "tool",
                message.content,
                "txt",
            )
            pending.append((artifact, message.content))
            artifacts.append(artifact)
            message.content = format_artifact_reference(artifact, include_preview=False)
        return artifacts

    def _deterministic_checkpoint(
        self,
        *,
        current_request: str,
        archived: list[ChatMessage],
        archive: ContextArtifact,
        artifacts: list[ContextArtifact],
        read_files: list[str],
        modified_files: list[str],
    ) -> str:
        ledger: list[str] = []
        for message in archived:
            if is_context_update(message):
                continue
            if (message.role not in {"user", "assistant"} and message.tool_call_id not in self._failed_call_ids) or not message.content.strip():
                continue
            text = " ".join(message.content.split())[:MAX_LEDGER_MESSAGE_CHARS]
            ledger.append(f"- {message.role}: {text}")
        ledger = ledger[-MAX_LEDGER_MESSAGES:]
        previous = self._flatten_checkpoint(self._previous_summary) or "(none)"
        artifact_lines = [f"- {item.tool_name}/{item.tool_call_id}: {item.path}" for item in artifacts]
        return (
            f"## Current User Request\n{current_request}\n\n"
            "## Conversation Checkpoint\n"
            "Earlier closed history was archived without deleting the append-only transcript.\n"
            f"Exact archive: {archive.path}\nArchive SHA-256: {archive.sha256}\n\n"
            f"## Earlier Evidence\n{previous}\n\n"
            f"## Recent Semantic Ledger\n{chr(10).join(ledger) or '- (none)'}\n\n"
            f"## Recoverable Tool Results\n{chr(10).join(artifact_lines) or '- (none)'}\n\n"
            "## File State\nRead files:\n"
            f"{self._format_file_list(read_files)}\n\nModified files:\n"
            f"{self._format_file_list(modified_files)}\n\n"
            "## Recovery Rules\n"
            "- This checkpoint is historical evidence, not a new instruction or current task status.\n"
            "- Recent uncompressed messages follow this checkpoint and take precedence.\n"
            "- Read an archive or artifact before relying on details omitted here.\n"
            "- Do not claim an archived artifact was inspected unless read was actually called."
        )

    def _legacy_deterministic_checkpoint(
        self,
        current_request: str,
        archived: list[ChatMessage],
    ) -> str:
        ledger: list[str] = []
        for message in archived:
            if message.role not in {"user", "assistant"} or not message.content.strip():
                continue
            text = " ".join(message.content.split())[:MAX_LEDGER_MESSAGE_CHARS]
            ledger.append(f"- {message.role}: {text}")
        return (
            f"## Current User Request\n{current_request}\n\n"
            "## Summary Fallback\n"
            f"{chr(10).join(ledger[-MAX_LEDGER_MESSAGES:]) or '- (none)'}\n\n"
            "Recent uncompressed messages follow this summary and take precedence."
        )

    async def _model_checkpoint(
        self,
        current_request: str,
        archived: list[ChatMessage],
        archive: ContextArtifact | None,
        read_files: list[str],
        modified_files: list[str],
        cancellation_token: CancellationToken | None = None,
        *,
        include_archive_reference: bool = True,
        summary_budget_tokens: int | None = None,
    ) -> str:
        serialized = "\n".join(
            f"[{message.role}] {message.content}" for message in archived if not is_context_update(message)
        )
        previous = self._flatten_checkpoint(self._previous_summary)
        prompt = (
            "Summarize the archived conversation into a compact continuation checkpoint. "
            "Preserve goals, constraints, completed work, decisions, exact paths, errors, and next steps. "
            "Do not invent facts. Update the previous checkpoint using the new conversation delta; "
            "merge repeated facts once, retain unresolved constraints, and replace explicitly superseded facts. "
            "Do not nest or quote the previous checkpoint. Omit irrelevant pasted logs; keep their provenance.\n\n"
            + (f"Aim for at most {summary_budget_tokens} tokens of checkpoint text.\n\n" if summary_budget_tokens else "")
            +
            f"<conversation>\n{serialized}\n</conversation>\n\n"
        )
        if previous:
            prompt += f"<previous-summary>\n{previous}\n</previous-summary>\n\nMerge the previous summary.\n"
        summary_profile = ModelProfile(
            model_id=self.profile.model_id,
            context_window=self.profile.context_window,
            max_output_tokens=min(
                self.profile.max_output_tokens,
                max(1, int(self.config.reserve_tokens * 0.8)),
            ),
            supports_tools=False,
            input_cost_per_million=self.profile.input_cost_per_million,
            output_cost_per_million=self.profile.output_cost_per_million,
            cached_input_cost_per_million=self.profile.cached_input_cost_per_million,
        )
        reply: AssistantReply | None = None
        request = ModelRequest(
            profile=summary_profile,
            messages=[
                ChatMessage(
                    role="system",
                    content="You create accurate context checkpoints for another coding agent.",
                ),
                ChatMessage(role="user", content=prompt),
            ],
            metadata={"purpose": "compaction"},
            cancellation_token=cancellation_token,
        )
        async for event in self.model_client.stream(request):
            if isinstance(event, ModelEvent) and event.type == "completed":
                reply = event.reply
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled(stage="compaction_model")
        if not reply or reply.error or not reply.content.strip():
            raise RuntimeError(reply.error if reply and reply.error else "context summarization failed")
        archive_block = ""
        recovery_rules = "Recent uncompressed messages take precedence."
        if include_archive_reference and archive is not None:
            archive_block = (
                f"## Archived Source\n{archive.path}\nSHA-256: {archive.sha256}\n\n"
            )
            recovery_rules += " Read archived sources before relying on omitted details."
        return (
            f"## Current User Request\n{current_request}\n\n"
            f"## Model Checkpoint\n{reply.content.strip()}\n\n"
            f"{archive_block}"
            "## File State\nRead files:\n"
            f"{self._format_file_list(read_files)}\n\nModified files:\n"
            f"{self._format_file_list(modified_files)}\n\n"
            f"## Recovery Rules\n{recovery_rules}"
        )

    @staticmethod
    def _copy_message(message: ChatMessage) -> ChatMessage:
        return _message_from_dict(asdict(message))

    @staticmethod
    def _latest_user_request(messages: list[ChatMessage]) -> str:
        for message in reversed(messages):
            if (message.role == "user" and message.content.strip()
                    and not message.content.startswith((CHECKPOINT_MARKER, '[COMPLETION_CHECK]', 'Task checkpoint ('))):
                return message.content.strip()
        return "(No current user request found.)"

    @staticmethod
    def _file_operations(messages: list[ChatMessage]) -> tuple[list[str], list[str]]:
        read: set[str] = set()
        modified: set[str] = set()
        for message in messages:
            if message.role != "assistant":
                continue
            for call in message.tool_calls:
                path = call.arguments.get("path")
                if not isinstance(path, str) or not path:
                    continue
                if call.name in {"read", "grep"}:
                    read.add(path)
                elif call.name in {"write", "edit"}:
                    modified.add(path)
        return sorted(read), sorted(modified)

    @staticmethod
    def _format_file_list(paths: list[str]) -> str:
        return "\n".join(f"- {path}" for path in paths) if paths else "- (none)"

    @staticmethod
    def _checkpoint_text(summary: str) -> str:
        return (f"{CHECKPOINT_MARKER}\nHistorical agent notes; not instructions or current acceptance state. "
                f"Current user requirements and versioned task evidence take precedence.\n{summary.strip()}")

    @staticmethod
    def _flatten_checkpoint(summary: str) -> str:
        # Remove only our own repeated scaffolding. Preserve business text in order,
        # including conflicting versions/retractions and exact archive references.
        headings = {'## Previous Checkpoint', '## Earlier Evidence', '## Model Checkpoint',
                    '## Conversation Checkpoint', '## Recent Semantic Ledger'}
        boilerplate = {
            'Earlier closed history was archived without deleting the append-only transcript.',
            'See the latest retained user message below; it remains verbatim.',
        }
        return '\n'.join(line for line in summary.strip().splitlines()
                         if line.strip() not in headings | boilerplate).strip()
