from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .retrieval import HybridMemoryRetriever, MemoryDocument, RetrievalHit, search_memory_documents


MAX_RESOURCE_BYTES = 64 * 1024


@dataclass(slots=True, frozen=True)
class SkillInfo:
    name: str
    description: str
    directory: Path


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
        self.skills = self._discover()

    def _discover(self) -> dict[str, SkillInfo]:
        if not self.root.exists():
            return {}
        result: dict[str, SkillInfo] = {}
        for path in sorted(self.root.glob("*/SKILL.md")):
            metadata, _ = _frontmatter(path.read_text(encoding="utf-8"))
            name = metadata.get("name", path.parent.name).strip()
            if name:
                result[name] = SkillInfo(name, metadata.get("description", "").strip(), path.parent.resolve())
        return result

    def list(self) -> list[dict[str, str]]:
        return [{"name": item.name, "description": item.description} for item in self.skills.values()]

    def search(
        self,
        query: str,
        retriever: HybridMemoryRetriever,
        limit: int = 3,
        *,
        use_cross_encoder: bool = True,
    ) -> list[RetrievalHit]:
        documents: list[MemoryDocument] = []
        for item in self.skills.values():
            documents.append(
                MemoryDocument(
                    record_id=item.name,
                    category="procedure",
                    subject=item.description,
                    content=self.read(item.name),
                )
            )
        return search_memory_documents(
            retriever,
            query,
            documents,
            min(max(1, limit), 5),
            use_cross_encoder=use_cross_encoder,
        )

    def catalog_for_prompt(self, limit: int = 20) -> str:
        if not self.skills:
            return ""
        lines = ["<available_skills>", "Procedural guides. Read a relevant skill before using its workflow."]
        lines.extend(
            f"- {item.name}: {item.description}"
            for item in list(self.skills.values())[: max(1, min(limit, 50))]
        )
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
