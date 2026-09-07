from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any
from collections.abc import Callable

from MiniClaw.coding_agent.tools.base import ToolResult

from .archive import ArchiveMemoryIndex
from .evidence import MemoryEvidenceStore
from .episodic import EpisodicMemoryStore
from .procedural import ProceduralMemoryStore
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
    user_messages_provider: Callable[[], list[str]] | None = None

    name = "memory"
    description = (
        "Hybrid-search stable semantic memory, compressed transcript/tool archives, and episodic "
        "session summaries. Use archive_search iteratively when automatically injected excerpts do "
        "not contain every reasoning hop. Use overview to inspect the FOUR modules: working, episodic, "
        "semantic, procedural. preference/project/environment/fact are categories within semantic, "
        "not separate modules. read honors category and limit. conflict_list lists registered conflicts "
        "only, not a complete consistency audit. Preference writes need an exact user evidence quote. "
        "Never store secrets or temporary command output."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "overview",
                    "inspect",
                    "read",
                    "search",
                    "remember",
                    "replace",
                    "forget",
                    "conflict_list",
                    "conflict_resolve",
                    "archive_search",
                    "episode_list",
                    "episode_search",
                    "episode_read",
                ],
            },
            "category": {
                "type": "string",
                "enum": ["preference", "project", "environment", "fact"],
            },
            "module": {"type": "string", "enum": ["working", "episodic", "semantic", "procedural"]},
            "evidence": {"type": "string", "description": "Exact user quote supporting a preference write"},
            "content": {"type": "string"},
            "oldContent": {"type": "string"},
            "query": {"type": "string"},
            "sessionId": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1},
            "conflictPolicy": {
                "type": "string",
                "enum": ["reject", "keep_existing", "replace", "keep_both"],
            },
            "conflictId": {"type": "string"},
            "resolution": {
                "type": "string",
                "enum": ["keep_existing", "replace", "keep_both"],
            },
            "status": {"type": "string", "enum": ["pending", "resolved", "all"]},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        action = arguments["action"]
        if action in {"overview", "inspect"}:
            if self.view_provider is None:
                raise ValueError("Memory module inspection is not connected")
            module = arguments.get("module") if action == "inspect" else None
            if action == "inspect" and not module:
                raise ValueError("inspect requires module: working, episodic, semantic, or procedural")
            value = self.view_provider(module, int(arguments.get("limit", 5)))
            return ToolResult(json.dumps(value, ensure_ascii=False, indent=2))
        if action == "read":
            return ToolResult(self.semantic.read(arguments.get("category"), arguments.get("limit")))
        if action in {"remember", "replace"} and arguments.get("category") == "preference":
            users = self.user_messages_provider() if self.user_messages_provider else []
            quote = str(arguments.get("evidence") or "")
            verified_quote = preference_evidence(users, quote) if quote else None
            if verified_quote is None:
                raise ValueError("Preference writes require evidence quoting an explicit preference from the user; assistant behavior or memory output is not evidence.")
            # Persist the user's words, not an amplified assistant paraphrase.
            arguments = {**arguments, "content": verified_quote, "evidence": verified_quote}
        if action in {"remember", "replace"} and historical_state_claim(str(arguments.get("content") or "")):
            raise ValueError("File existence and completion claims belong in dated episode history, not stable semantic memory. Verify current workspace state when needed.")
        if action == "search":
            query = arguments["query"]
            limit = int(arguments.get("limit", 5))
            scope = "semantic"
            cached = self.query_tracker.cached(scope, query, limit)
            if cached is not None:
                progress = self.query_tracker.cache_progress(cached)
                value = attach_search_metadata(cached.payload, progress)
                return ToolResult(
                    json.dumps(value, ensure_ascii=False, indent=2),
                    details={"query_dedup": progress.metadata()},
                )
            hits = self.semantic.search(query, limit)
            value = [
                {
                    "recordId": hit.document.record_id,
                    "category": hit.document.category,
                    "content": hit.document.content,
                    "score": round(hit.score, 6),
                    "rrfScore": round(hit.rrf_score, 8),
                    "bm25Score": round(hit.bm25_score, 6),
                    "vectorScore": round(hit.vector_score, 6),
                    "bm25Rank": hit.bm25_rank,
                    "vectorRank": hit.vector_rank,
                    "deterministicScore": round(hit.deterministic_score, 6),
                    "crossEncoderScore": (
                        round(hit.cross_encoder_score, 6)
                        if hit.cross_encoder_score is not None
                        else None
                    ),
                    "supersededRecordIds": list(hit.superseded_record_ids),
                }
                for hit in hits
            ]
            progress = self.query_tracker.observe(
                scope,
                query,
                limit,
                {str(item["recordId"]) for item in value},
                value,
            )
            return ToolResult(
                json.dumps(attach_search_metadata(value, progress), ensure_ascii=False, indent=2),
                details={"query_dedup": progress.metadata()},
            )
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
            result = self.semantic.replace(
                arguments["category"], arguments["oldContent"], arguments["content"]
            )
            self._record_semantic_evidence(
                arguments["category"],
                arguments["content"],
                action="replace",
                evidence_quote=str(arguments.get("evidence") or ""),
            )
            return ToolResult(result)
        if action == "forget":
            return ToolResult(self.semantic.forget(arguments["category"], arguments["content"]))
        if action == "conflict_list":
            value = [
                asdict(conflict)
                for conflict in self.semantic.list_conflicts(arguments.get("status", "pending"))
            ]
            return ToolResult(json.dumps(value, ensure_ascii=False, indent=2))
        if action == "conflict_resolve":
            conflict = next(
                (
                    item
                    for item in self.semantic.list_conflicts("pending")
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
            return ToolResult(result)
        if action == "archive_search":
            session_id = str(arguments.get("sessionId") or self.current_session_id)
            query = arguments["query"]
            limit = int(arguments.get("limit", 5))
            scope = f"archive:{session_id}"
            cached = self.query_tracker.cached(scope, query, limit)
            if cached is not None:
                progress = self.query_tracker.cache_progress(cached)
                value = attach_search_metadata(cached.payload, progress)
                return ToolResult(
                    json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                    details={"query_dedup": progress.metadata()},
                )
            parent_limit = min(limit, 5)
            hits = self.archive.search_parents(
                query,
                parent_limit=parent_limit,
                neighbor_radius=1,
                session_id=session_id,
            )
            value = [
                {
                    "parentId": hit.parent_id,
                    "anchorRecordId": hit.anchor.record_id,
                    "recordIds": [record.record_id for record in hit.records],
                    "chunkIndices": [record.chunk_index for record in hit.records],
                    "messageIndex": hit.anchor.message_index,
                    "content": hit.content,
                    "sourcePath": hit.anchor.source_path,
                    "sourceKind": hit.anchor.source_kind,
                    "role": hit.anchor.role,
                    "actionBlockId": hit.anchor.action_block_id,
                    "eventKinds": list(dict.fromkeys(record.event_kind for record in hit.records)),
                    "score": round(hit.score, 6),
                    "deterministicScore": round(hit.deterministic_score, 6),
                    "crossEncoderScore": (
                        round(hit.cross_encoder_score, 6)
                        if hit.cross_encoder_score is not None
                        else None
                    ),
                    "supersededRecordIds": list(hit.superseded_record_ids),
                }
                for hit in hits
            ]
            progress = self.query_tracker.observe(
                scope,
                query,
                limit,
                {str(item["parentId"]) for item in value},
                value,
            )
            return ToolResult(
                json.dumps(
                    attach_search_metadata(value, progress),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                details={"query_dedup": progress.metadata()},
            )
        if action == "episode_list":
            value = self.episodic.list(int(arguments.get("limit", 5)))
        elif action == "episode_search":
            value = self.episodic.search(arguments["query"], int(arguments.get("limit", 5)))
        elif action == "episode_read":
            return ToolResult(self.episodic.read(arguments["sessionId"]))
        else:
            raise ValueError(f"Unknown memory action: {action}")
        return ToolResult(json.dumps(value, ensure_ascii=False, indent=2))

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


@dataclass(slots=True)
class SkillTool:
    procedural: ProceduralMemoryStore

    name = "skill"
    description = "List procedural skills or read a skill guide/resource on demand."
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "read"]},
            "name": {"type": "string"},
            "resource": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        if arguments["action"] == "list":
            return ToolResult(json.dumps(self.procedural.list(), ensure_ascii=False, indent=2))
        return ToolResult(
            self.procedural.read(arguments["name"], arguments.get("resource", "SKILL.md"))
        )
