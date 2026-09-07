from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from MiniClaw.llm.types import ChatMessage
from MiniClaw.agent.context import is_context_update

from .retrieval import HybridMemoryRetriever, MemoryDocument, search_memory_documents


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_session_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    return normalized[:80] or "default"


class EpisodicMemoryStore:
    def __init__(
        self,
        root: Path,
        retriever: HybridMemoryRetriever | None = None,
    ) -> None:
        self.root = root
        self.retriever = retriever or HybridMemoryRetriever()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, session_id: str) -> Path:
        return self.root / f"{_safe_session_id(session_id)}.md"

    def checkpoint(
        self,
        session_id: str,
        messages: list[ChatMessage],
        *,
        summary: str = "",
        status: str = "active",
    ) -> None:
        users = [message.content for message in messages if message.role == "user" and message.content]
        assistants = [message.content for message in messages if message.role == "assistant" and message.content and not is_context_update(message)]
        goal = users[-1] if users else "No explicit user request."
        outcome = assistants[-1] if assistants else "No assistant outcome yet."
        body = summary.strip() or (
            f"## Goal\n\n{goal}\n\n## Latest Outcome\n\n{outcome}\n\n"
            "## Open Work\n\nContinue from the Working Context if this session is resumed."
        )
        path = self.path_for(session_id)
        started = _now()
        if path.exists():
            match = re.search(r"^- Started:\s*(.+)$", path.read_text(encoding="utf-8"), re.M)
            if match:
                started = match.group(1).strip()
        content = (
            f"# Session {_safe_session_id(session_id)}\n\n"
            f"- Started: {started}\n- Updated: {_now()}\n- Status: {status}\n\n{body.rstrip()}\n"
        )
        path.write_text(content, encoding="utf-8")

    def list(self, limit: int = 5) -> list[dict[str, str]]:
        files = sorted(self.root.glob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)
        result: list[dict[str, str]] = []
        for path in files[: max(1, min(limit, 50))]:
            text = path.read_text(encoding="utf-8")
            match = re.search(r"^- Status:\s*(.+)$", text, re.M)
            result.append({"sessionId": path.stem, "status": match.group(1).strip() if match else "unknown"})
        return result

    def read(self, session_id: str) -> str:
        path = self.path_for(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Episode not found: {path.stem}")
        return path.read_text(encoding="utf-8")

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        use_cross_encoder: bool = True,
    ) -> list[dict[str, object]]:
        documents: list[MemoryDocument] = []
        content_by_session: dict[str, str] = {}
        for path in self.root.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            goal_match = re.search(
                r"^## Goal\s*$\s*(.+?)(?=^## |\Z)",
                text,
                re.MULTILINE | re.DOTALL,
            )
            goal = " ".join(goal_match.group(1).split()) if goal_match else path.stem
            status_match = re.search(r"^- Status:\s*(.+)$", text, re.MULTILINE)
            updated_match = re.search(r"^- Updated:\s*(.+)$", text, re.MULTILINE)
            status = status_match.group(1).strip() if status_match else "unknown"
            updated_at = updated_match.group(1).strip() if updated_match else ""
            content_by_session[path.stem] = text
            documents.append(
                MemoryDocument(
                    record_id=path.stem,
                    category="episode",
                    subject=goal,
                    content=text,
                    created_at=updated_at,
                    status=status,
                    confidence=1.0 if status == "completed" else 0.7,
                    source_kind="episode",
                )
            )
        hits = search_memory_documents(
            self.retriever,
            query,
            documents,
            limit,
            use_cross_encoder=use_cross_encoder,
        )
        return [
            {
                "sessionId": hit.document.record_id,
                "subject": hit.document.subject,
                "content": content_by_session[hit.document.record_id],
                "preview": " ".join(content_by_session[hit.document.record_id].split())[:300],
                "score": hit.score,
                "rrfScore": hit.rrf_score,
                "bm25Score": hit.bm25_score,
                "vectorScore": hit.vector_score,
                "bm25Rank": hit.bm25_rank,
                "vectorRank": hit.vector_rank,
                "deterministicScore": hit.deterministic_score,
                "crossEncoderScore": hit.cross_encoder_score,
                "status": hit.document.status,
                "updatedAt": hit.document.created_at,
                "confidence": hit.document.confidence,
            }
            for hit in hits
        ]
