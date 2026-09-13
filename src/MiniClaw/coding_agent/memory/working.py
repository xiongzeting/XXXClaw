from __future__ import annotations

import json
import hashlib
import re
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from MiniClaw.cancellation import CancellationToken, OperationCancelledError
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

from .artifacts import ARTIFACT_MARKER, ContextArtifact, ContextArtifactStore, format_artifact_reference
from .config import (
    ACTIVE_COMPACTION_POLICY,
    MemoryConfig,
)
from .read_cache import ReadSnapshotCache


CHECKPOINT_MARKER = "[MiniClaw Working Context Checkpoint]"
MAX_LEDGER_MESSAGES = 12
MAX_LEDGER_MESSAGE_CHARS = 1_600
COMPACTION_PROTOCOL_VERSION = 4
COMPACTION_PIPELINE = ("prepare", "summarize", "commit")
COMPACTION_FLOW = ("trigger", "safe_cut", *COMPACTION_PIPELINE, "recover")
# These tools produce system-managed instructions or derived views.  Their
# output may be useful in the live turn, but it is not durable memory evidence.
NON_MEMORY_TOOL_NAMES = frozenset({"memory", "skill"})
# Fallback/compatibility ceiling for old checkpoints.  Hard compaction now
# derives the model-summary budget from the pre-compaction request size.
HARD_SUMMARY_TOKENS = 5_000
_VERIFICATION_COMMAND = re.compile(
    r"(?:pytest|unittest|verify|test|check|py_compile|compile|lint|assert|diff|cmp)",
    re.IGNORECASE,
)


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
        archive_index: object | None = None,
    ) -> None:
        self.path = path
        self.session_id = session_id
        self.config = config
        self.model_client = model_client
        self.profile = profile
        # Kept as a constructor compatibility argument for older callers. The
        # closed-history archive pipeline is intentionally no longer used.
        self.archive_index = None
        self.artifacts = ContextArtifactStore(workspace, session_id, config.artifact_preview_chars)
        self._active_ids: list[str | None] = []
        self._previous_summary = ""
        self.last_compaction_decision: dict[str, Any] = {}
        # Kept as one guard for an unchanged prefix.  It prevents repeatedly
        # paying for a model summary that cannot reclaim useful context.
        self._deferred_prefix: str | None = None
        self._failed_call_ids: set[str] = set()
        self.read_snapshots = ReadSnapshotCache(workspace, min(8192, max(1024, config.target_tokens * 2)))
        self.last_projection: dict[str, Any] = {}
        # Input cost is accumulated between committed compactions.  The
        # provider's per-request context size alone cannot catch a long tool
        # loop whose history stays below the hard context limit.
        self._cumulative_input_tokens = 0
        # Keep the pressure ledger for diagnostics; it is reset only after a
        # committed hard compaction.
        self._pressure_input_tokens = 0

    @property
    def cumulative_input_tokens(self) -> int:
        return self._cumulative_input_tokens

    @property
    def pressure_input_tokens(self) -> int:
        return self._pressure_input_tokens

    def record_model_input(self, input_tokens: int) -> int:
        """Add one provider-reported request to the current cost window."""
        try:
            value = max(0, int(input_tokens))
        except (TypeError, ValueError):
            value = 0
        self._cumulative_input_tokens += value
        self._pressure_input_tokens += value
        self._append_event({
            "type": "input_cost_window",
            "cumulative_input_tokens": self._cumulative_input_tokens,
            "pressure_input_tokens": self._pressure_input_tokens,
            "status": "open",
        })
        return self._cumulative_input_tokens

    def reset_input_cost_window(self) -> None:
        """Start a fresh cumulative window after a committed compaction."""
        self._cumulative_input_tokens = 0
        self._append_event({
            "type": "input_cost_window",
            "cumulative_input_tokens": 0,
            "pressure_input_tokens": self._pressure_input_tokens,
            "status": "reset_after_compaction",
        })

    def reset_pressure_window(self) -> None:
        """Reset pressure only after a committed hard compaction."""
        self._pressure_input_tokens = 0
        self._append_event({
            "type": "input_cost_window",
            "cumulative_input_tokens": self._cumulative_input_tokens,
            "pressure_input_tokens": 0,
            "status": "pressure_reset_after_hard_compaction",
        })

    def _append_event(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**payload, "timestamp": payload.get("timestamp") or _now()}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def append_message(self, message: ChatMessage) -> str:
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
            self._previous_summary = ""
            return []

        records: list[tuple[str, ChatMessage]] = []
        latest_compaction: dict[str, Any] | None = None
        latest_input_cost: int | None = None
        latest_pressure: int | None = None
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
                    continue
                if payload.get("type") == "input_cost_window":
                    try:
                        latest_input_cost = max(0, int(payload.get("cumulative_input_tokens") or 0))
                        latest_pressure = max(0, int(payload.get("pressure_input_tokens") or 0))
                    except (TypeError, ValueError):
                        latest_input_cost = 0
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
            first_kept = latest_compaction.get("first_kept_message_id")
            compaction_details = latest_compaction.get("details") or {}
            if (
                first_kept is None
                and compaction_details.get("reason") == "phase_boundary"
            ):
                self._previous_summary = str(latest_compaction.get("summary") or "")
                if self._previous_summary:
                    messages.append(
                        ChatMessage(
                            role="assistant",
                            content=self._checkpoint_text(self._previous_summary),
                        )
                    )
                    ids.append(None)
                start = len(records)
                found_cut = True
            else:
                found_cut = False
                for index, (message_id, _) in enumerate(records):
                    if message_id == first_kept:
                        start = index
                        found_cut = True
                        break
            if found_cut:
                if self._previous_summary == "":
                    self._previous_summary = str(latest_compaction.get("summary") or "")
                if self._previous_summary and not messages:
                    messages.append(
                        ChatMessage(
                            role="assistant",
                            content=self._checkpoint_text(self._previous_summary),
                        )
                    )
                    ids.append(None)
            else:
                # A torn/partial compaction event is ignored; the raw message
                # records remain the only trustworthy recovery source.
                latest_compaction = None
                self._previous_summary = ""

        for message_id, message in records[start:]:
            messages.append(message)
            ids.append(message_id)
        self._active_ids = ids
        if latest_input_cost is not None:
            self._cumulative_input_tokens = latest_input_cost
        if latest_pressure is not None:
            self._pressure_input_tokens = latest_pressure
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
        # Persist oversized call arguments at the same tool boundary as the
        # result.  This covers large write/edit payloads even though the
        # assistant message containing the call was already appended before
        # execution began; the next request projection replaces its large
        # leaves with the recoverable artifact reference.
        argument_artifact: ContextArtifact | None = None
        if call.name not in NON_MEMORY_TOOL_NAMES:
            try:
                rendered_arguments = json.dumps(
                    call.arguments, ensure_ascii=False, sort_keys=True, default=str
                )
                if len(rendered_arguments.encode("utf-8")) >= self.config.artifact_threshold_bytes:
                    argument_artifact = self.artifacts.save(
                        f"{call.call_id}-arguments",
                        f"{call.name}-arguments",
                        rendered_arguments,
                        "json",
                    )
                    result.details = {
                        **result.details,
                        "context_argument_artifact": argument_artifact.to_dict(),
                    }
            except (TypeError, ValueError):
                # Argument serialization is observability-only and must never
                # turn a successful tool call into a tool failure.
                argument_artifact = None
        if (
            result.is_error
            or ARTIFACT_MARKER in result.content
            or call.name in NON_MEMORY_TOOL_NAMES
        ):
            if result.is_error:
                self._failed_call_ids.add(call.call_id)
                self._append_event({"type": "tool_status", "call_id": call.call_id, "failed": True})
            return result
        # An artifact is already the recovery representation of a large tool
        # result. Reading that file is an explicit recovery operation, not a
        # new large-tool event; otherwise a read-back could immediately create
        # a second artifact and defeat compaction.
        if call.name == "read" and self._is_context_artifact_path(call.arguments.get("path")):
            result.details = {**result.details, "artifact_read": True}
            return result
        # Persist genuinely large results at delivery time.  Production
        # configuration clamps this threshold to the 1K-token boundary while
        # tests may still construct a deliberately smaller MemoryConfig.
        if len(result.content.encode("utf-8")) < self.config.artifact_threshold_bytes:
            return result
        artifact = self.artifacts.save(call.call_id, call.name, result.content, "txt")
        details = {**result.details, "context_artifact": artifact.to_dict()}
        return ToolResult(
            content=self._artifact_reference(artifact),
            is_error=result.is_error,
            details=details,
        )

    def transform_request_context(self, messages: list[ChatMessage], *, persist: bool = True,
                                  include_snapshots: bool = True) -> list[ChatMessage]:
        """Return a bounded copy for model requests, preserving raw history/audit."""
        messages = ContextJournal.project(messages)
        transformed = [self._copy_message(message) for message in messages]
        for message in transformed:
            if message.content.startswith(CHECKPOINT_MARKER):
                message.role = 'assistant'
        calls: dict[str, ToolInvocation] = {}
        for message in transformed:
            if message.role == "assistant":
                calls.update({call.call_id: call for call in message.tool_calls})
        matched = {
            message.tool_call_id
            for message in transformed
            if message.role == "tool" and message.tool_call_id in calls
        }
        completed = matched - self._failed_call_ids
        threshold = self.config.artifact_threshold_bytes
        store = self.artifacts.save if persist else self.artifacts.prepare
        latest_tool = {}
        duplicated = {}
        duplicate_reads = 0
        resolved_failures: set[str] = set()
        pending_failures: list[str] = []
        for message in transformed:
            if message.role != "tool" or message.tool_call_id not in calls:
                continue
            call = calls[message.tool_call_id]
            if message.tool_call_id in self._failed_call_ids:
                pending_failures.append(message.tool_call_id)
                continue
            command = " ".join(str(call.arguments.get("command") or "").split())
            if call.name == "bash" and _VERIFICATION_COMMAND.search(command):
                resolved_failures.update(pending_failures)
                pending_failures.clear()
        for message in reversed(transformed):
            if message.role != "tool" or message.tool_call_id not in matched:
                continue
            call = calls[message.tool_call_id]
            if call.name not in {"read", "bash", "grep", "search"}:
                continue
            key = (json.dumps(call.arguments, sort_keys=True, ensure_ascii=False),
                   hashlib.sha256(message.content.encode("utf-8")).hexdigest())
            if key in latest_tool and len(message.content) > 256:
                duplicated[call.call_id] = latest_tool[key]
                if call.name == "read":
                    duplicate_reads += 1
            else:
                latest_tool[key] = call.call_id

        for message in transformed:
            if message.role == "assistant":
                for call in message.tool_calls:
                    if call.call_id not in matched:
                        continue
                    call.arguments = self._artifactize_tool_arguments(
                        call, call.arguments, store, threshold
                    )
                    if call.call_id in duplicated and call.name in {"bash", "grep", "search"}:
                        call.arguments = {"repeated_call": duplicated[call.call_id]}
                    elif call.call_id in resolved_failures and call.name in {"bash", "grep", "search", "read"}:
                        if call.name == "bash":
                            command = " ".join(str(call.arguments.get("command") or "").split())
                            call.arguments = {
                                "command": "[resolved failed tool call; full arguments retained in trace] " + command[:180]
                            }
                        else:
                            call.arguments = {"path": str(call.arguments.get("path") or "")}
                    elif call.call_id in self._failed_call_ids and call.name == "edit":
                        call.arguments = self._compact_failed_edit_arguments(call.arguments)
            elif message.role == "tool" and message.tool_call_id in resolved_failures:
                compact = " ".join(message.content.split())[:320]
                message.content = (
                    "[Resolved tool error; later verification passed. "
                    "Full error remains in the raw trace.] " + compact
                )
            elif message.role == "tool" and message.tool_call_id in completed and not message.content.startswith("[MiniClaw context artifact]"):
                if message.tool_call_id in duplicated:
                    message.content = ("[Historical duplicate tool result; identical arguments and returned content. "
                                       f"Full result retained at tool call {duplicated[message.tool_call_id]}.]")
                    continue
                if not message.content:
                    continue
                if len(message.content.encode("utf-8")) >= threshold:
                    call = calls[message.tool_call_id]
                    if call.name == "read" and self._is_context_artifact_path(call.arguments.get("path")):
                        # This is already an explicit artifact recovery read;
                        # do not wrap the recovered bytes in another artifact.
                        continue
                    artifact = store(call.call_id, call.name, message.content, "txt")
                    reference = self._artifact_reference(artifact)
                    if len(reference.encode("utf-8")) < len(message.content.encode("utf-8")):
                        message.content = reference
        # Tool history is projected at the hard compaction boundary; this
        # request projection never creates a historical archive pass.
        if include_snapshots:
            before_replay_tokens = estimate_context_tokens(transformed)
            raw_tokens = estimate_context_tokens(messages)
            transformed, snapshot_details = self.read_snapshots.project(transformed)
            self.last_projection = {**snapshot_details, "duplicate_read_results_removed": duplicate_reads,
                                    "duplicate_tool_results_removed": len(duplicated),
                                    'stable_history_messages': 0,
                                    "raw_history_tokens": raw_tokens,
                                    "history_before_replay_tokens": before_replay_tokens,
                                    "history_projection_saved_tokens": raw_tokens - before_replay_tokens,
                                    "read_replay_added_tokens": estimate_context_tokens(transformed) - before_replay_tokens,
                                    "history_net_added_tokens": estimate_context_tokens(transformed) - raw_tokens,
                                    "request_history_tokens": estimate_context_tokens(transformed)}
        return transformed

    def _is_context_artifact_path(self, value: Any) -> bool:
        if not isinstance(value, str) or not value:
            return False
        try:
            candidate = (self.artifacts.workspace / value).resolve()
            candidate.relative_to(self.artifacts.root.resolve())
            return candidate.is_file()
        except (OSError, ValueError):
            return False

    def _artifactize_tool_arguments(
        self,
        call: ToolInvocation,
        value: Any,
        store: Any,
        threshold: int,
        path: str = "root",
    ) -> Any:
        """Persist oversized tool-call payloads before they are replayed.

        Write/edit payloads can be much larger than their result.  Keep the
        argument shape intact while replacing only oversized string leaves by
        recoverable artifact references; the original append-only transcript is
        unchanged because this runs on the projected copy.
        """
        if isinstance(value, str):
            if len(value.encode("utf-8")) < threshold:
                return value
            artifact = store(
                f"{call.call_id}-arg-{path}",
                f"{call.name}-arguments",
                value,
                "txt",
            )
            reference = self._artifact_reference(artifact)
            return reference if len(reference.encode("utf-8")) < len(value.encode("utf-8")) else value
        if isinstance(value, list):
            return [
                self._artifactize_tool_arguments(call, item, store, threshold, f"{path}[{index}]")
                for index, item in enumerate(value)
            ]
        if isinstance(value, dict):
            return {
                key: self._artifactize_tool_arguments(call, item, store, threshold, f"{path}.{key}")
                for key, item in value.items()
            }
        return value

    @staticmethod
    def _compact_failed_edit_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        """Keep failed edit history actionable without replaying its full payload."""
        edits = arguments.get("edits")
        if not isinstance(edits, list):
            return {"path": arguments.get("path"), "failed": True, "payload": "omitted"}
        compact: list[dict[str, Any]] = []
        for item in edits[:16]:
            if not isinstance(item, dict):
                compact.append({"payload": "omitted"})
                continue
            row: dict[str, Any] = {}
            for key in ("oldText", "newText"):
                value = item.get(key)
                if isinstance(value, str):
                    row[key] = {
                        "chars": len(value),
                        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest()[:16],
                    }
                else:
                    row[key] = "omitted"
            compact.append(row)
        return {"path": arguments.get("path"), "failed": True, "edits": compact,
                "omitted_edits": max(0, len(edits) - len(compact))}

    async def maybe_compact(
        self,
        messages: list[ChatMessage],
        provider_input_tokens: int = 0,
        cancellation_token: CancellationToken | None = None,
        *,
        cumulative_input_tokens: int | None = None,
        pressure_input_tokens: int | None = None,
    ) -> CompactionOutcome | None:
        self.last_compaction_decision = {"phase": "trigger", "reason": "checking"}
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled(stage="compaction_start")
        if not self.config.enabled:
            self.last_compaction_decision = {"phase": "trigger", "reason": "disabled"}
            return None
        if len(messages) < 2:
            self.last_compaction_decision = {
                "phase": "trigger",
                "reason": "insufficient_messages",
            }
            return None
        # Hard pressure is measured from the current provider request. The
        # cumulative/pressure ledgers remain diagnostic state and are reset
        # only after a successful hard compaction.
        tokens_before = (
            provider_input_tokens
            if provider_input_tokens
            else cumulative_input_tokens
        ) or estimate_context_tokens(messages)
        pressure_before = pressure_input_tokens or self._pressure_input_tokens or tokens_before
        if pressure_before < self.config.hard_trigger_tokens:
            self.last_compaction_decision = {
                "phase": "trigger",
                "reason": "below_hard_trigger",
                "tokens_before": tokens_before,
                "pressure_tokens": pressure_before,
            }
            return None

        # Hard pressure is the only automatic compaction point. It produces a
        # structured continuation summary plus the most recent 1/10 of the
        # pre-compaction request history.
        return await self._compact(
            messages,
            tokens_before=tokens_before,
            reason="hard",
            cancellation_token=cancellation_token,
            force_model=True,
        )

    async def _compact(
        self,
        messages: list[ChatMessage],
        *,
        tokens_before: int,
        reason: str,
        cancellation_token: CancellationToken | None = None,
        force_model: bool = False,
    ) -> CompactionOutcome | None:
        """Run the one progressive compaction pipeline.

        Raw session messages remain append-only. Large individual tool results
        are content-addressed artifacts; the final ``compaction`` event is the
        commit marker used during recovery. There is no closed-history archive
        or old-tool archive stage.
        """
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled(stage="compaction_prepare")

        first_kept = self._find_cut_point(
            messages,
            keep_recent_tokens=max(1, tokens_before // 10) if reason == "hard" else None,
        )
        if first_kept <= 0:
            self.last_compaction_decision = {"phase": "prepare", "reason": "no_safe_cut"}
            return None

        archived = [
            self._copy_message(message)
            for message in messages[:first_kept]
            if message.role != "system" and not message.content.startswith(CHECKPOINT_MARKER)
        ]
        retained = messages[first_kept:]
        if not archived or not retained:
            self.last_compaction_decision = {"phase": "prepare", "reason": "no_compactable_prefix"}
            return None

        first_kept_id = self._durable_message_id(first_kept)
        if not first_kept_id:
            self.last_compaction_decision = {"phase": "prepare", "reason": "no_durable_cut"}
            return None

        # Estimate the same history view that is sent to the provider. Fixed
        # system/tool/retrieval overhead is not falsely counted as reclaimable.
        projected = self.transform_request_context(
            messages, persist=False, include_snapshots=False
        )
        projected_retained = self.transform_request_context(
            retained, persist=False, include_snapshots=False
        )
        local_before = estimate_context_tokens(projected)
        retained_tokens = estimate_context_tokens(projected_retained)
        if not force_model and (
            local_before <= self.config.target_tokens
            and tokens_before < self.profile.context_window - self.config.reserve_tokens
        ):
            self.last_compaction_decision = {
                "phase": "prepare",
                "reason": "history_within_target",
                "request_history_tokens": local_before,
                "provider_input_tokens": tokens_before,
            }
            return None

        minimum_saving = max(1, min(256, local_before // 20))
        prefix_key = self._compaction_key(archived)
        if prefix_key == self._deferred_prefix:
            self.last_compaction_decision = {
                "phase": "prepare",
                "reason": "unchanged_low_gain_prefix",
            }
            return None

        semantic_tokens = (
            sum(
                estimate_message_tokens(message)
                for message in archived
                if self._is_summary_evidence(message)
            )
            if reason == "hard"
            else 0
        )

        # Prepare only oversized individual tool results. The complete old
        # conversation stays in the append-only session log; it is not copied
        # into a second transcript store.
        archived_for_summary, artifacts, pending_artifacts = self._prepare_summary_messages(archived)

        # The latest retained request is already verbatim in the tail. Avoid
        # copying a large pasted request into every checkpoint.
        current_request = (
            "See the latest retained user message below; it remains verbatim."
            if any(message.role == "user" for message in retained)
            else self._latest_user_request(messages)
        )
        if len(current_request) > MAX_LEDGER_MESSAGE_CHARS:
            current_request = (
                "The current request is retained in the recent original tail. "
                "Preserve its goals and constraints in the checkpoint."
            )
        read_files, modified_files = self._file_operations(archived)
        # Capture a bounded hash manifest without copying file contents into
        # the checkpoint. The manifest is shown after the summary; read
        # snapshots remain a one-time recovery layer for the next request.
        recovery_files = self.read_snapshots.capture_paths([*modified_files, *read_files])
        deterministic = self._deterministic_checkpoint(
            current_request=current_request,
            archived=archived,
            artifacts=artifacts,
            read_files=read_files,
            modified_files=modified_files,
        )
        deterministic_after = estimate_context_tokens(
            [
                ChatMessage(role="assistant", content=self._checkpoint_text(deterministic)),
                *projected_retained,
            ]
        )

        use_model = force_model or reason == "hard"
        strategy = "model-summary" if use_model else "deterministic-summary"
        summary = deterministic
        model_summary_error: str | None = None
        model_summary_rejected: str | None = None
        # Keep the continuation checkpoint close to one tenth of the
        # pre-compaction request.  This is a target, not a second fixed
        # context layer; the required sections and safety checks still apply.
        summary_budget_tokens = max(256, tokens_before // 10)
        if use_model:
            try:
                model_summary = await self._model_checkpoint(
                    current_request,
                    archived_for_summary,
                    read_files,
                    modified_files,
                    cancellation_token,
                    summary_budget_tokens=summary_budget_tokens,
                )
                model_after = estimate_context_tokens(
                    [
                        ChatMessage(
                            role="assistant",
                            content=self._checkpoint_text(model_summary),
                        ),
                        *projected_retained,
                    ]
                )
                if (
                    model_after <= self.config.target_tokens
                    and local_before - model_after >= minimum_saving
                ):
                    summary = model_summary
                    strategy = "model-summary"
                else:
                    # A model response that expands the context is not a
                    # successful compression, even if its prose is plausible.
                    model_summary_rejected = "not_smaller_or_over_target"
            except OperationCancelledError:
                # Cancellation is a caller-visible abort, not a summary
                # failure.  Do not commit a checkpoint after it.
                raise
            except Exception as exc:
                # The raw transcript remains the recovery source; use the
                # deterministic continuation summary if the model fails.
                model_summary_error = str(exc)
                if any(
                    marker in model_summary_error
                    for marker in ("omitted required continuation state", "invalid active status")
                ):
                    model_summary_rejected = "invalid_model_checkpoint"
                if force_model:
                    model_summary_rejected = model_summary_rejected or "model_summary_failed"
                strategy = "deterministic-fallback"

        estimated_after = estimate_context_tokens(
            [
                ChatMessage(role="assistant", content=self._checkpoint_text(summary)),
                *projected_retained,
            ]
        )
        # Keep the checkpoint itself bounded even when the deterministic
        # fallback contains many ledger entries. Four characters per token is
        # the same conservative estimate used throughout this module.
        summary = self._preserve_edges(summary, summary_budget_tokens * 4)
        recovery_manifest = self._recovery_manifest(recovery_files, artifacts)
        if recovery_manifest:
            summary = f"{summary.rstrip()}\n\n## Recovery Manifest\n{recovery_manifest}"
        estimated_after = estimate_context_tokens(
            [
                ChatMessage(role="assistant", content=self._checkpoint_text(summary)),
                *projected_retained,
            ]
        )
        if local_before - estimated_after < minimum_saving:
            self.last_compaction_decision = {
                "phase": "summarize",
                "reason": "insufficient_token_savings",
                "estimated_history_before": local_before,
                "estimated_history_after": estimated_after,
                "minimum_saving_tokens": minimum_saving,
            }
            self._deferred_prefix = prefix_key
            return None

        # A failed or oversized model summary falls back to the deterministic
        # continuation summary. Hard compaction must remain available even if
        # the auxiliary summary request fails.

        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled(stage="compaction_commit")

        # Commit boundary: no cancellation checks are inserted between these
        # idempotent writes and the final append-only marker.
        for artifact, content in pending_artifacts:
            self.artifacts.persist(artifact, content)

        compaction_id = uuid.uuid4().hex
        layers: list[str] = []
        if artifacts:
            layers.append("tool-result-artifact")
        layers.extend(["recent-original", strategy])
        if strategy == "deterministic-fallback":
            layers.append("deterministic-summary-fallback")
        if model_summary_rejected is not None:
            layers.append("deterministic-summary-kept")
        details: dict[str, Any] = {
            "version": COMPACTION_PROTOCOL_VERSION,
            "pipeline": list(COMPACTION_PIPELINE),
            "flow": list(COMPACTION_FLOW),
            "phase": "committed",
            "reason": reason,
            "strategy": strategy,
            "summary_mode": strategy,
            "compaction_policy": ACTIVE_COMPACTION_POLICY,
            "layers_applied": layers,
            "first_kept_index": first_kept,
            "recent_original_target_tokens": max(1, tokens_before // 10),
            "summary_target_tokens": summary_budget_tokens,
            "compacted_prefix_message_count": len(archived),
            "summary_input_message_count": len(archived_for_summary),
            "retained_message_count": len(retained),
            "tool_artifacts": [artifact.to_dict() for artifact in artifacts],
            "recovery_files": recovery_files,
            "semantic_tokens": semantic_tokens,
            "estimated_tokens_after": estimated_after,
            "estimated_history_tokens_after": estimated_after,
            "estimated_history_tokens_before": local_before,
            "estimated_history_tokens_saved": local_before - estimated_after,
            "minimum_saving_tokens": minimum_saving,
            "transcript_retained": True,
            **(
                {"model_summary_error": model_summary_error}
                if model_summary_error is not None
                else {}
            ),
            **(
                {"model_summary_rejected": model_summary_rejected}
                if model_summary_rejected is not None
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
        # A compacted request no longer contains the old Read pair. Give the
        # next request one verified replay of up to five key files, then
        # suppress repeats.
        self.read_snapshots.reset_replay()
        self._deferred_prefix = None
        self.reset_input_cost_window()
        self.reset_pressure_window()
        self.last_compaction_decision = {
            "phase": "commit",
            "reason": "committed",
            "strategy": strategy,
        }
        kept_ids = (
            self._active_ids[first_kept:]
            if len(self._active_ids) >= len(messages)
            else [None] * len(retained)
        )
        compacted_messages = [ChatMessage(role="assistant", content=self._checkpoint_text(summary)), *retained]
        self._active_ids = [None, *kept_ids]
        return CompactionOutcome(
            messages=compacted_messages,
            tokens_before=tokens_before,
            estimated_tokens_after=estimated_after,
            strategy=strategy,
            details=details,
        )

    def _durable_message_id(self, index: int) -> str | None:
        """Return the log id at a cut, or refuse a non-durable projection."""
        if index < 0 or index >= len(self._active_ids):
            return None
        for value in self._active_ids[index:]:
            if value:
                return value
        return None

    def _compaction_key(self, messages: list[ChatMessage]) -> str:
        payload = [self._previous_summary, [asdict(message) for message in messages]]
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _is_managed_tool_message(message: ChatMessage) -> bool:
        return message.role == "tool" and message.name in NON_MEMORY_TOOL_NAMES

    def _is_summary_evidence(self, message: ChatMessage) -> bool:
        """Keep task evidence, excluding derived memory/skill output."""
        if is_context_update(message) or self._is_managed_tool_message(message):
            return False
        if message.role == "assistant" and message.tool_calls:
            # A call to memory/skill is a routing operation, not a fact. Keep
            # mixed assistant turns only when they also contain ordinary work.
            ordinary_calls = [
                call for call in message.tool_calls
                if call.name not in NON_MEMORY_TOOL_NAMES
            ]
            if not ordinary_calls and not message.content.strip():
                return False
        return bool(message.content.strip() or message.tool_calls)

    def _prepare_summary_messages(
        self,
        messages: list[ChatMessage],
    ) -> tuple[list[ChatMessage], list[ContextArtifact], list[tuple[ContextArtifact, str]]]:
        """Prepare bounded tool references for the compaction summary."""
        prepared: list[ChatMessage] = []
        artifacts: list[ContextArtifact] = []
        pending: list[tuple[ContextArtifact, str]] = []
        seen_artifacts: set[tuple[str, str]] = set()
        for message in messages:
            if (
                is_context_update(message)
                or self._is_managed_tool_message(message)
            ):
                # Journal updates, Skill and memory responses are derived
                # views. They can stay in the raw transcript but never become
                # summary evidence.
                continue
            prepared_message = self._copy_message(message)
            if prepared_message.role == "assistant" and prepared_message.tool_calls:
                prepared_message.tool_calls = [
                    call for call in prepared_message.tool_calls
                    if call.name not in NON_MEMORY_TOOL_NAMES
                ]
                if not prepared_message.tool_calls and not prepared_message.content.strip():
                    continue
            if (
                prepared_message.role == "tool"
                and prepared_message.content
                and ARTIFACT_MARKER not in prepared_message.content
                and prepared_message.tool_call_id not in self._failed_call_ids
                and len(prepared_message.content.encode("utf-8")) >= self.config.artifact_threshold_bytes
            ):
                artifact = self.artifacts.prepare(
                    prepared_message.tool_call_id or uuid.uuid4().hex,
                    prepared_message.name or "tool",
                    prepared_message.content,
                    "txt",
                )
                artifact_key = (artifact.path, artifact.sha256)
                if artifact_key not in seen_artifacts:
                    pending.append((artifact, prepared_message.content))
                    artifacts.append(artifact)
                    seen_artifacts.add(artifact_key)
                prepared_message.content = self._artifact_reference(artifact)
            prepared.append(prepared_message)
        return prepared, artifacts, pending

    @staticmethod
    def _artifact_reference(artifact: ContextArtifact, *, include_preview: bool = True) -> str:
        """Use the configured artifact preview at every context boundary."""
        if not include_preview:
            return format_artifact_reference(artifact, include_preview=False)
        return format_artifact_reference(artifact, include_preview=True)

    def _find_cut_point(
        self,
        messages: list[ChatMessage],
        *,
        keep_recent_tokens: int | None = None,
    ) -> int:
        accumulated = 0
        approximate = len(messages) - 1
        recent_budget = (
            self.config.keep_recent_tokens
            if keep_recent_tokens is None
            else keep_recent_tokens
        )
        for index in range(len(messages) - 1, -1, -1):
            accumulated += estimate_message_tokens(messages[index])
            approximate = index
            if accumulated >= recent_budget:
                break
        # Mark boundaries that are not inside an unfinished tool batch.  A
        # user message normally follows a complete batch, but recovery and
        # tests can hand us a partially written transcript; never archive half
        # of that protocol unit.
        pending: set[str] = set()
        safe_before: list[bool] = []
        for message in messages:
            safe_before.append(not pending)
            if message.role == "assistant":
                pending.update(call.call_id for call in message.tool_calls if call.call_id)
            elif message.role == "tool" and message.tool_call_id:
                pending.discard(message.tool_call_id)

        # Prefer complete turns. This is stricter than merely avoiding a tool-result cut.
        for index in range(approximate, len(messages)):
            if messages[index].role == "user" and safe_before[index]:
                return index
        # A single oversized turn may be split only before an assistant message. Its following
        # tool results stay on the retained side, so call/result pairs cannot be separated.
        for index in range(approximate, len(messages)):
            if (
                messages[index].role == "assistant"
                and not is_context_update(messages[index])
                and safe_before[index]
            ):
                return index
        return 0

    def _deterministic_checkpoint(
        self,
        *,
        current_request: str,
        archived: list[ChatMessage],
        artifacts: list[ContextArtifact],
        read_files: list[str],
        modified_files: list[str],
    ) -> str:
        ledger: list[str] = []
        for message in archived:
            if not self._is_summary_evidence(message):
                continue
            if (
                message.role not in {"user", "assistant"}
                and message.tool_call_id not in self._failed_call_ids
            ) or not message.content.strip():
                continue
            text = " ".join(message.content.split())[:MAX_LEDGER_MESSAGE_CHARS]
            ledger.append(f"- {message.role}: {text}")
        ledger = ledger[-MAX_LEDGER_MESSAGES:]
        previous = self._flatten_checkpoint(self._previous_summary) or "(none)"
        previous = self._preserve_edges(previous, MAX_LEDGER_MESSAGE_CHARS * 4)
        artifact_lines = [
            f"- {item.tool_name}/{item.tool_call_id}: {item.path}" for item in artifacts
        ]
        return (
            f"## Current User Request\n{current_request}\n\n"
            "## Continuation Checkpoint\n"
            "Status: IN_PROGRESS\n"
            f"## Earlier Evidence\n{previous}\n\n"
            f"## Recent Semantic Ledger\n{chr(10).join(ledger) or '- (none)'}\n\n"
            f"## Recoverable Tool Results\n{chr(10).join(artifact_lines) or '- (none)'}\n\n"
            "## File State\nRead files:\n"
            f"{self._format_file_list(read_files)}\n\nModified files:\n"
            f"{self._format_file_list(modified_files)}\n\n"
            "## Recovery Rules\n"
            "- Treat completed work and the latest verification as the current task state.\n"
            "- Recent uncompressed messages follow this checkpoint and take precedence.\n"
            "- A file proof with the same SHA-256 means the file is unchanged; do not reread it.\n"
            "- After a PASS with no later write/edit, reuse that result instead of rerunning the same check.\n"
            "- Do not reread unchanged files or repeat a passing check unless newer work invalidates it.\n"
            "- Continue from the unresolved item instead of restarting the task.\n"
            "- Read an artifact before relying on details omitted from its compact reference.\n"
            "- Do not claim an artifact was inspected unless read was actually called."
        )

    async def _model_checkpoint(
        self,
        current_request: str,
        archived: list[ChatMessage],
        read_files: list[str],
        modified_files: list[str],
        cancellation_token: CancellationToken | None = None,
        *,
        summary_budget_tokens: int | None = None,
    ) -> str:
        serialized = self._serialize_summary_messages(archived)
        previous = self._preserve_edges(self._flatten_checkpoint(self._previous_summary), 12_000)
        prompt = (
            "Create an executable continuation checkpoint for another coding agent. This is a state transfer, "
            "not a narrative summary. The task is still in progress because this checkpoint is created inside "
            "the agent loop. Preserve only facts needed to finish correctly.\n\n"
            "Return exactly these sections:\n"
            "Status: IN_PROGRESS\n"
            "## Current objective\n"
            "## Confirmed constraints\n"
            "## Completed work\n"
            "## Latest valid verification\n"
            "## Remaining or failed\n"
            "## Next action\n"
            "## Necessary files and artifacts\n\n"
            "Rules:\n"
            "- Do not invent facts or convert an attempted action into completed work.\n"
            "- Use real tool results to distinguish success from failure.\n"
            "- Keep only the latest verification relevant to the latest modification; label it PASS or FAIL.\n"
            "- Preserve unresolved errors, rollback requirements, compatibility rules, prohibited paths, and exact filenames.\n"
            "- Merge repeated facts once and remove superseded failures or decisions when later evidence resolves them.\n"
            "- Give one concrete smallest next action. Never tell the next agent to restart or broadly re-inspect the task.\n"
            "- Do not request rereading unchanged files or rerunning an already passing check unless later work invalidated it.\n"
            "- Omit pasted logs and prose explanations; retain an artifact path when exact details were omitted.\n"
            "- Do not quote or nest the previous checkpoint.\n\n"
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
        required_sections = (
            "## Current objective",
            "## Confirmed constraints",
            "## Completed work",
            "## Latest valid verification",
            "## Remaining or failed",
            "## Next action",
            "## Necessary files and artifacts",
        )
        if not all(section in reply.content for section in required_sections):
            raise RuntimeError("context summarization omitted required continuation state")
        if not re.search(r"(?im)^Status:\s*IN_PROGRESS\s*$", reply.content):
            raise RuntimeError("context summarization returned an invalid active status")
        return (
            f"## Current User Request\n{current_request}\n\n"
            f"## Continuation State\n{reply.content.strip()}\n\n"
            "## File State\nRead files:\n"
            f"{self._format_file_list(read_files)}\n\nModified files:\n"
            f"{self._format_file_list(modified_files)}\n\n"
            "## Recovery Rules\n"
            "Recent uncompressed messages take precedence. Continue from the stated next action; "
            "do not restart completed work or repeat unchanged verification."
        )

    def _serialize_summary_messages(self, messages: list[ChatMessage]) -> str:
        """Bound auxiliary input at message boundaries, never mid message."""
        rows = [
            f"[{message.role}] {message.content}"
            for message in messages
            if self._is_summary_evidence(message)
        ]
        serialized = "\n".join(rows)
        available_tokens = max(
            256,
            self.profile.context_window - self.config.reserve_tokens - 256,
        )
        # Keep the summary request comfortably below the provider window.  A
        # floor avoids erasing all evidence in the small synthetic profiles
        # used by tests and local models.
        max_chars = min(
            max(2_048, self.config.keep_recent_tokens * 4),
            max(2_048, available_tokens * 3),
        )
        if len(serialized) <= max_chars:
            return serialized
        head_budget = max(512, max_chars // 3)
        tail_budget = max(512, max_chars - head_budget - 96)
        head_rows: list[str] = []
        used = 0
        for row in rows:
            if used + len(row) + 1 > head_budget:
                break
            head_rows.append(row)
            used += len(row) + 1
        tail_rows: list[str] = []
        used = 0
        for row in reversed(rows):
            if used + len(row) + 1 > tail_budget:
                break
            tail_rows.append(row)
            used += len(row) + 1
        tail_rows.reverse()
        return "\n".join(
            [*head_rows, "[older messages omitted; source remains in the append-only session log]", *tail_rows]
        )

    @staticmethod
    def _copy_message(message: ChatMessage) -> ChatMessage:
        return _message_from_dict(asdict(message))

    @staticmethod
    def _preserve_edges(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        head = max(1, (limit * 2) // 3)
        tail = max(1, limit - head - 64)
        return f"{value[:head]}\n…[checkpoint middle omitted]…\n{value[-tail:]}"

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
    def _recovery_manifest(
        recovery_files: list[dict[str, Any]],
        artifacts: list[ContextArtifact],
    ) -> str:
        """Render a compact integrity index; never inline recovered contents."""
        rows: list[str] = []
        for item in recovery_files:
            path = str(item.get("path") or "")
            digest = str(item.get("sha256") or "")
            if path and digest:
                rows.append(
                    f"- read {path} | sha256={digest} | bytes={int(item.get('bytes') or 0)}"
                )
        for artifact in artifacts:
            rows.append(
                f"- artifact {artifact.path} | sha256={artifact.sha256} | bytes={artifact.byte_size}"
            )
        return "\n".join(rows)

    @staticmethod
    def _checkpoint_text(summary: str) -> str:
        return (
            f"{CHECKPOINT_MARKER}\n"
            "System-generated continuation state from the prior transcript. Preserve completed work, "
            "continue only unresolved items, and apply any newer user message as the latest requirement.\n"
            f"{summary.strip()}"
        )

    @staticmethod
    def _flatten_checkpoint(summary: str) -> str:
        # Remove only our own repeated scaffolding. Preserve business text in order,
        # including conflicting versions and retractions.
        headings = {'## Previous Checkpoint', '## Earlier Evidence', '## Model Checkpoint',
                    '## Conversation Checkpoint', '## Continuation Checkpoint',
                    '## Continuation State', '## Recent Semantic Ledger'}
        boilerplate = {'See the latest retained user message below; it remains verbatim.'}
        return '\n'.join(line for line in summary.strip().splitlines()
                         if line.strip() not in headings | boilerplate
                         and "closed history archive" not in line.casefold()).strip()
