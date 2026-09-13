from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from collections.abc import Callable

from MiniClaw.coding_agent.tools.base import ToolResult

from .archive import ArchiveMemoryIndex
from .evidence import MemoryEvidenceStore
from .episodic import EpisodicMemoryStore
from .query_tracker import QueryTracker, attach_search_metadata
from .semantic import MemoryConflictError, SemanticMemoryStore
from .quality import historical_state_claim, preference_evidence


@dataclass(slots=True)
class MemoryTool:
    semantic: SemanticMemoryStore
    episodic: EpisodicMemoryStore
    archive: ArchiveMemoryIndex
    current_session_id: str
    query_tracker: QueryTracker = field(default_factory=QueryTracker)
    evidence: MemoryEvidenceStore | None = None
    source_path: str = ""
    user_scope: str = ""
    channel_scope: str = ""
    workspace_scope: str = ""
    view_provider: Callable[[str | None, int], dict[str, Any]] | None = None
    diagnostics_provider: Callable[[], dict[str, Any]] | None = None
    user_messages_provider: Callable[[], list[str]] | None = None
    retrieve_provider: Callable[[str, int], list[Any]] | None = None
    cache_guard: Callable[[], None] | None = None
    on_memory_mutation: Callable[[], None] | None = None

    name = "memory"
    description = "只在用户明确要求记忆操作时使用。search 查 episodic；remember/replace/forget 操作 semantic。"
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "search 查 episodic；remember 写 semantic；replace 修改 semantic；forget 删除 semantic。用户明确要求时再调用。",
                "enum": [
                    "search",
                    "remember",
                    "replace",
                    "forget",
                ],
            },
            "category": {
                "type": "string",
                "enum": ["preference", "project", "environment", "fact"],
            },
            "evidence": {"type": "string"},
            "content": {"type": "string"},
            "oldContent": {"type": "string", "description": "replace/forget 的旧内容别名；forget 不填 content 可清空指定类别或全部 semantic。"},
            "recordId": {"type": "string"},
            "expectedRevision": {"type": "integer", "minimum": 1},
            "query": {"type": "string", "description": "search 使用的具体历史任务查询；不要用作工作区或工具结果搜索。"},
            "limit": {"type": "integer", "minimum": 1},
            "conflictPolicy": {
                "type": "string",
                "enum": ["reject", "keep_existing", "replace", "keep_both"],
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        action = arguments["action"]
        # The public schema exposes one global search. Keep source-based search
        # as an internal compatibility path for existing callers and tests.
        # The public memory mutation API is intentionally semantic-only. Episodic and archive
        # records are append-only/system-managed, while working context is owned by the context
        # projector. Keeping this invariant here prevents the model from selecting a backing file.
        if action in {"remember", "replace", "forget"} and arguments.get("module") not in (None, "semantic"):
            raise ValueError("memory mutations are only supported for semantic memory")
        if action in {"remember", "replace"} and arguments.get("category") == "preference":
            users = self.user_messages_provider() if self.user_messages_provider else []
            quote = str(arguments.get("evidence") or "")
            verified_quote = preference_evidence(users, quote) if quote else None
            if verified_quote is None:
                # Evidence is a guardrail, not a formatting challenge.  If the
                # current user turn clearly states a preference, accept that
                # turn even when the model paraphrased the evidence wrapper.
                verified_quote = preference_evidence(users)
                if verified_quote is None and re.search(r"用户(?:原话|明确说)|user\s+said", quote, re.I):
                    verified_quote = preference_evidence([quote])
            if verified_quote is None:
                raise ValueError("Preference writes require evidence of an explicit preference from the user")
            # Persist the user's words, not an amplified assistant paraphrase.
            arguments = {**arguments, "content": verified_quote, "evidence": verified_quote}
        if action in {"remember", "replace"} and historical_state_claim(str(arguments.get("content") or "")):
            raise ValueError("File existence and completion claims belong in dated episode history, not stable semantic memory. Verify current workspace state when needed.")
        if action == "search":
            if self.cache_guard:
                self.cache_guard()
            query = str(arguments.get("query") or "").strip()
            if not query:
                raise ValueError("memory search requires a specific episodic-memory query")
            limit = int(arguments.get("limit", 5))
            scope = "episodic"
            cached = self.query_tracker.cached(scope, query, limit)
            if cached is not None:
                progress = self.query_tracker.cache_progress(cached)
                value = attach_search_metadata(cached.payload, progress)
                return ToolResult(
                    json.dumps(value, ensure_ascii=False, indent=2),
                    details={"query_dedup": progress.metadata()},
                )
            if self.retrieve_provider is None:
                raise ValueError("episodic memory search is not connected")
            items = self.retrieve_provider(query, max(1, min(limit, 20)))
            value = [
                {
                    "sessionId": str(item.record_id),
                    "content": str(item.content),
                    "score": round(float(item.fused_score), 8),
                    **{
                        key: item.metadata[key]
                        for key in (
                            "subject", "status", "created_at", "confidence",
                            "bm25_score", "vector_score", "bm25_rank", "vector_rank",
                            "rrf_score", "age_days", "recency_multiplier",
                        )
                        if key in item.metadata
                    },
                }
                for item in items
            ]
            progress = self.query_tracker.observe(
                scope,
                query,
                limit,
                {str(item.get("sessionId") or item.get("recordId") or "") for item in value},
                value,
            )
            return ToolResult(
                json.dumps(attach_search_metadata(value, progress), ensure_ascii=False, indent=2),
                details={"query_dedup": progress.metadata()},
            )
        if action in {"inspect", "overview"}:
            if self.view_provider is None:
                raise ValueError("memory inspection is not connected")
            module = arguments.get("module") if action == "inspect" else None
            value = self.view_provider(module, int(arguments.get("limit", 5)))
            return ToolResult(json.dumps(value, ensure_ascii=False, indent=2))
        if action == "diagnostics":
            if self.diagnostics_provider is None:
                raise ValueError("memory diagnostics are not connected")
            return ToolResult(json.dumps(self.diagnostics_provider(), ensure_ascii=False, indent=2))
        if action == "read":
            category = arguments.get("category")
            valid = {"preference", "project", "environment", "fact"}
            if category is not None and category not in valid:
                raise ValueError("Unknown semantic memory category")
            entries = self.semantic.entries()
            selected = [category] if category else list(entries)
            limit = max(1, min(int(arguments.get("limit", 5)), 50))
            lines = ["Semantic memory"]
            for name in selected:
                values = entries[name]
                shown = values[:limit]
                lines.extend([f"\n[{name}] Showing {len(shown)} of {len(values)}"])
                lines.extend(f"- {value}" for value in shown)
            return ToolResult("\n".join(lines))
        if action == "remember":
            try:
                content = self.semantic.remember(
                    arguments["category"],
                    arguments["content"],
                    arguments.get("conflictPolicy", "reject"),
                )
                self._record_semantic_evidence(
                    arguments["category"],
                    arguments["content"],
                    action="remember",
                    evidence_quote=str(arguments.get("evidence") or ""),
                )
                if self._changed_result(content):
                    self._notify_mutation()
                return ToolResult(content)
            except MemoryConflictError as exc:
                return ToolResult(
                    json.dumps(
                        {
                            "error": "memory_conflict",
                            "conflict": asdict(exc.conflict),
                            "allowedResolutions": ["keep_existing", "replace", "keep_both"],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    is_error=True,
                    details={"memory_conflict": asdict(exc.conflict)},
                )
        if action == "replace":
            if arguments.get("recordId"):
                result = self.semantic.update_record(
                    arguments["recordId"],
                    int(arguments["expectedRevision"]) if arguments.get("expectedRevision") is not None else None,
                    arguments["content"], arguments["category"]
                )
                self._record_semantic_evidence(
                    arguments["category"], arguments["content"], action="replace",
                    evidence_quote=str(arguments.get("evidence") or ""),
                )
                if self._changed_result(result):
                    self._notify_mutation()
                return ToolResult(result)
            if not arguments.get("oldContent"):
                raise ValueError("replace requires recordId or oldContent to identify the semantic record")
            result = self.semantic.replace(
                arguments["category"], arguments["oldContent"], arguments["content"]
            )
            self._record_semantic_evidence(
                arguments["category"],
                arguments["content"],
                action="replace",
                evidence_quote=str(arguments.get("evidence") or ""),
            )
            if self._changed_result(result):
                self._notify_mutation()
            return ToolResult(result)
        if action == "forget":
            category = arguments.get("category")
            content = arguments.get("content") or arguments.get("oldContent") or ""
            if not content and not arguments.get("recordId"):
                return ToolResult(self.semantic.forget_all(category))
            if not category:
                raise ValueError("forget one record requires category")
            result = self.semantic.forget(
                category, content,
                record_id=arguments.get("recordId"),
                expected_revision=arguments.get("expectedRevision"),
            )
            self._notify_mutation()
            return ToolResult(result)
        # Kept only for old local callers; these actions are not in the
        # model-visible schema and therefore do not enlarge the tool contract.
        if action == "conflict_list":
            value = [
                asdict(conflict)
                for conflict in self.semantic.list_conflicts(arguments.get("status", "pending"))
            ]
            return ToolResult(json.dumps(value, ensure_ascii=False, indent=2))
        if action == "conflict_resolve":
            conflict = next(
                (
                    item for item in self.semantic.list_conflicts("pending")
                    if item.conflict_id == arguments["conflictId"]
                ),
                None,
            )
            result = self.semantic.resolve_conflict(
                arguments["conflictId"], arguments["resolution"]
            )
            if conflict is not None and arguments["resolution"] in {"replace", "keep_both"}:
                self._record_semantic_evidence(
                    conflict.category,
                    conflict.proposed,
                    action=f"conflict_{arguments['resolution']}",
                )
                self._notify_mutation()
            return ToolResult(result)
        if action == "archive_search":
            raise ValueError("archive recall is disabled; use automatic, semantic, or episodic memory")
        if action == "episode_list":
            value = self.episodic.list(int(arguments.get("limit", 5)))
        elif action == "episode_search":
            value = self.episodic.search(arguments["query"], int(arguments.get("limit", 5)))
        elif action == "episode_read":
            return ToolResult(self.episodic.read(arguments["sessionId"]))
        else:
            raise ValueError(f"Unknown memory action: {action}")
        return ToolResult(json.dumps(value, ensure_ascii=False, indent=2))

    def _notify_mutation(self) -> None:
        if self.on_memory_mutation:
            self.on_memory_mutation()

    @staticmethod
    def _changed_result(result: str) -> bool:
        return not result.startswith(("Memory already exists", "Kept existing"))

    def _record_semantic_evidence(
        self,
        category: str,
        content: str,
        *,
        action: str,
        evidence_quote: str = "",
    ) -> None:
        """Record provenance only when the proposed value is now active."""

        if self.evidence is None:
            return
        normalized = SemanticMemoryStore._normalize(content)
        active = any(
            SemanticMemoryStore._normalize(item).casefold() == normalized.casefold()
            for item in self.semantic.entries().get(category, [])
        )
        if not active:
            return
        self.evidence.append(
            kind="semantic",
            content=normalized,
            session_id=self.current_session_id,
            source_path=self.source_path,
            user_scope=self.user_scope,
            channel_scope=self.channel_scope,
            workspace_scope=self.workspace_scope,
            confidence=1.0,
            metadata={"category": category, "write_action": action, "evidence": evidence_quote},
        )
