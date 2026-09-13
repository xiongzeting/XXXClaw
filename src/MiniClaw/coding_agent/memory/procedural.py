from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .retrieval import HybridMemoryRetriever, MemoryDocument, RetrievalHit


MAX_RESOURCE_BYTES = 64 * 1024
MAX_FRONTMATTER_BYTES = 8 * 1024


@dataclass(slots=True, frozen=True)
class SkillInfo:
    name: str
    description: str
    directory: Path
    trigger_conditions: str = ""
    required_tools: str = ""
    forbidden_tools: str = ""
    risk_level: str = ""
    tags: str = ""


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    metadata: dict[str, str] = {}
    for line in text[4:end].splitlines():
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if match:
            metadata[match.group(1)] = match.group(2).strip().strip('"\'')
    return metadata, text[end + 5 :]


class ProceduralMemoryStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.candidate_path = self.root / ".candidates.jsonl"
        self.skills = self._discover()

    def refresh(self) -> None:
        """Refresh the lightweight catalog at a task boundary."""
        self.skills = self._discover()

    def _candidate_id(self, title: str, steps: list[str]) -> str:
        joined_steps = "\n".join(steps)
        raw = f"{title}\0{joined_steps}".encode("utf-8")
        return "procedure_candidate_" + hashlib.sha256(raw).hexdigest()[:16]

    def propose_candidate(self, title: str, steps: list[str], *, evidence: str = "", confidence: float = 0.0) -> str:
        if not title.strip() or len([step for step in steps if step.strip()]) < 2:
            raise ValueError("A procedure candidate needs a title and at least two steps")
        candidate_id = self._candidate_id(title, steps)
        existing = {item["candidate_id"] for item in self.list_candidates()}
        if candidate_id not in existing:
            self.root.mkdir(parents=True, exist_ok=True)
            with self.candidate_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"candidate_id": candidate_id, "title": title.strip(),
                    "steps": [step.strip() for step in steps if step.strip()], "evidence": evidence,
                    "confidence": max(0.0, min(1.0, confidence)), "status": "candidate",
                    "revision": 0, "created_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False) + "\n")
        return candidate_id

    def list_candidates(self, status: str = "") -> list[dict[str, object]]:
        if not self.candidate_path.exists():
            return []
        result = []
        for line in self.candidate_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not status or item.get("status") == status:
                result.append(item)
        return result

    def verify_candidate(self, candidate_id: str, verifier: str, evidence: str) -> dict[str, object]:
        candidates = self.list_candidates()
        item = next((candidate for candidate in candidates if candidate.get("candidate_id") == candidate_id), None)
        if item is None:
            raise ValueError("Procedure candidate was not found")
        item.update({"status": "verified", "verified_by": verifier, "verification_evidence": evidence})
        self._rewrite_candidates(candidates)
        return item

    def approve_candidate(self, candidate_id: str, approver: str) -> Path:
        candidates = self.list_candidates()
        item = next((candidate for candidate in candidates if candidate.get("candidate_id") == candidate_id), None)
        if item is None or item.get("status") != "verified":
            raise ValueError("Only verified procedure candidates can be approved")
        revision = int(item.get("revision") or 0) + 1
        name = str(item["title"]).strip().lower().replace(" ", "-")[:48] or candidate_id
        directory = self.root / name
        directory.mkdir(parents=True, exist_ok=True)
        body = "---\n" + f"name: {name}\nrevision: {revision}\nverified_by: {approver}\n" + "---\n\n"
        body += f"# {item['title']}\n\n" + "\n".join(f"{i}. {step}" for i, step in enumerate(item["steps"], 1)) + "\n"
        (directory / "SKILL.md").write_text(body, encoding="utf-8")
        item.update({"status": "approved", "revision": revision, "approved_by": approver, "skill_name": name})
        self._rewrite_candidates(candidates)
        self.skills = self._discover()
        return directory / "SKILL.md"

    def _rewrite_candidates(self, candidates: list[dict[str, object]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.candidate_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in candidates), encoding="utf-8")

    def _discover(self) -> dict[str, SkillInfo]:
        if not self.root.exists():
            return {}
        result: dict[str, SkillInfo] = {}
        for path in sorted(self.root.glob("*/SKILL.md")):
            # Catalog discovery only needs frontmatter.  Do not read the full
            # workflow body here; the selected SKILL.md is loaded once at the
            # task boundary (and once again after a committed compaction).
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                metadata, _ = _frontmatter(handle.read(MAX_FRONTMATTER_BYTES))
            name = metadata.get("name", path.parent.name).strip()
            if name:
                result[name] = SkillInfo(name, metadata.get("description", "").strip(), path.parent.resolve(),
                    metadata.get("trigger_conditions", metadata.get("triggers", "")).strip(),
                    metadata.get("required_tools", "").strip(), metadata.get("forbidden_tools", "").strip(),
                    metadata.get("risk_level", "").strip(), metadata.get("tags", "").strip())
        return result

    def list(self) -> list[dict[str, str]]:
        return [{"name": item.name, "description": item.description, "trigger_conditions": item.trigger_conditions,
                 "required_tools": item.required_tools, "forbidden_tools": item.forbidden_tools,
                 "risk_level": item.risk_level, "tags": item.tags} for item in self.skills.values()]

    def _catalog_scores(self, query: str) -> list[tuple[float, SkillInfo]]:
        query_normalized = query.casefold()
        query_tokens = set(
            re.findall(r"[A-Za-z0-9_.-]+|[\u4e00-\u9fff]+", query_normalized)
        )
        scored: list[tuple[float, SkillInfo]] = []
        for item in self.skills.values():
            fields = {
                "name": item.name.casefold(),
                "description": item.description.casefold(),
                "trigger": item.trigger_conditions.casefold(),
                "tools": item.required_tools.casefold(),
                "tags": item.tags.casefold(),
            }
            score = 100.0 if fields["name"] and fields["name"] in query_normalized else 0.0
            for key, weight in (
                ("name", 5.0), ("trigger", 4.0), ("description", 2.0),
                ("tools", 1.5), ("tags", 1.5),
            ):
                tokens = set(re.findall(r"[A-Za-z0-9_.-]+|[\u4e00-\u9fff]+", fields[key]))
                score += weight * len(query_tokens & tokens)
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], pair[1].name))
        return scored

    def search(
        self,
        query: str,
        retriever: HybridMemoryRetriever,
        limit: int = 3,
        *,
        use_cross_encoder: bool = True,
    ) -> list[RetrievalHit]:
        # Compatibility API: procedural routing is catalog-only. The retriever
        # argument is intentionally ignored so SKILL.md never becomes a BM25
        # or vector-memory candidate. Scores below are deterministic intent
        # scores from skill metadata, not memory-retrieval scores.
        scored = self._catalog_scores(query)
        results: list[RetrievalHit] = []
        for rank, (score, item) in enumerate(scored, start=1):
            document = MemoryDocument(
                record_id=item.name,
                category="procedure",
                subject=item.description,
                content=(
                    f"{item.name}\n{item.description}\n"
                    f"triggers: {item.trigger_conditions}\n"
                    f"tools: {item.required_tools}\n"
                    f"tags: {item.tags}"
                ),
            )
            results.append(
                RetrievalHit(
                    document=document,
                    score=score,
                    rrf_score=score,
                    bm25_score=0.0,
                    vector_score=0.0,
                    bm25_rank=rank,
                    vector_rank=None,
                    deterministic_score=score,
                )
            )
            if len(results) >= min(max(1, limit), 5):
                break
        return results

    def select_for_task(self, query: str, limit: int = 2) -> list[SkillInfo]:
        """Route skills with deterministic metadata intent matching only.

        A skill is loaded only when its name/description/trigger metadata
        matches the task. This keeps SKILL.md out of vector memory retrieval
        and avoids injecting an unrelated guide merely because the registry is
        non-empty.
        """
        if not self.skills or not query.strip():
            return []
        scored = self._catalog_scores(query)
        return [item for _, item in scored[: max(1, min(limit, 5))]]

    def catalog_for_prompt(self, limit: int = 20, max_chars: int = 2_400) -> str:
        if not self.skills:
            return ""
        lines = ["<available_skills>", "Procedural guides. Read a relevant skill before using its workflow."]
        remaining = max(200, max_chars - len("\n".join(lines)))
        for item in list(self.skills.values())[: max(1, min(limit, 50))]:
            description = item.description[:320]
            trigger = item.trigger_conditions[:180]
            line = f"- {item.name}: {description} (path={item.directory}; triggers={trigger})"
            if len(line) + 1 > remaining:
                break
            lines.append(line)
            remaining -= len(line) + 1
        lines.append("</available_skills>")
        return "\n".join(lines)

    def read(self, name: str, resource: str = "SKILL.md") -> str:
        item = self.skills.get(name)
        if not item:
            raise ValueError(f"Unknown skill: {name}")
        target = (item.directory / resource).resolve()
        try:
            target.relative_to(item.directory)
        except ValueError as exc:
            raise ValueError("Skill resource must stay inside the skill directory") from exc
        if not target.is_file():
            raise FileNotFoundError(f"Skill resource not found: {resource}")
        if target.stat().st_size > MAX_RESOURCE_BYTES:
            raise ValueError("Skill resource is too large")
        text = target.read_text(encoding="utf-8")
        if target.name == "SKILL.md":
            metadata, body = _frontmatter(text)
            return f"Skill: {metadata.get('name', name)}\n\n{body.strip()}"
        return text
