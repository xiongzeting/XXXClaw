from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


InstructionLevel = Literal["global", "workspace", "local"]
InstructionStatus = Literal["included", "truncated", "omitted"]


@dataclass(slots=True, frozen=True)
class InstructionSource:
    path: Path
    display_path: str
    scope: Path | None
    display_scope: str
    level: InstructionLevel
    depth: int
    sha256: str
    mtime_ns: int
    size_bytes: int
    estimated_tokens: int
    injected_tokens: int
    status: InstructionStatus
    content: str
    injected_content: str

    @property
    def injection_key(self) -> str:
        return f"{self.path}|{self.sha256}|{self.status}|{self.injected_tokens}"

    def applies_to(self, target: Path) -> bool:
        if self.scope is None:
            return True
        try:
            target.relative_to(self.scope)
            return True
        except ValueError:
            return False

    def to_trace(self) -> dict[str, object]:
        return {
            "path": self.display_path,
            "scope": self.display_scope,
            "level": self.level,
            "depth": self.depth,
            "sha256": self.sha256,
            "mtime_ns": self.mtime_ns,
            "size_bytes": self.size_bytes,
            "estimated_tokens": self.estimated_tokens,
            "injected_tokens": self.injected_tokens,
            "status": self.status,
        }


@dataclass(slots=True, frozen=True)
class InstructionResolution:
    prompt: str
    sources: tuple[InstructionSource, ...]
    budget_tokens: int
    used_tokens: int
    target_paths: tuple[str, ...]
    digest: str

    @property
    def injection_keys(self) -> frozenset[str]:
        return frozenset(
            source.injection_key
            for source in self.sources
            if source.status in {"included", "truncated"}
        )

    def relevant_injection_keys(self, target: Path) -> frozenset[str]:
        return frozenset(
            source.injection_key
            for source in self.sources
            if source.status in {"included", "truncated"} and source.applies_to(target)
        )

    def to_trace(self) -> dict[str, object]:
        return {
            "budget_tokens": self.budget_tokens,
            "used_tokens": self.used_tokens,
            "target_paths": list(self.target_paths),
            "digest": self.digest,
            "sources": [source.to_trace() for source in self.sources],
        }
