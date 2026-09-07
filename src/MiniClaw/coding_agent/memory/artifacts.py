from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


ARTIFACT_MARKER = "[MiniClaw context artifact]"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned[:80] or "artifact"


@dataclass(slots=True, frozen=True)
class ContextArtifact:
    tool_call_id: str
    tool_name: str
    path: str
    byte_size: int
    sha256: str
    preview: str
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ContextArtifactStore:
    def __init__(self, workspace: Path, session_id: str, preview_chars: int) -> None:
        self.workspace = workspace.resolve()
        self.root = self.workspace / ".aster" / "context-artifacts" / _safe_name(session_id)
        self.preview_chars = preview_chars

    def _preview(self, content: str) -> str:
        if len(content) <= self.preview_chars:
            return content
        half = max(1, self.preview_chars // 2)
        return f"{content[:half]}\n... [artifact preview truncated] ...\n{content[-half:]}"

    def save(self, identifier: str, name: str, content: str, extension: str) -> ContextArtifact:
        artifact = self.prepare(identifier, name, content, extension)
        self.persist(artifact, content)
        return artifact

    def prepare(self, identifier: str, name: str, content: str, extension: str) -> ContextArtifact:
        """Build a reference without I/O, for a compaction not yet accepted."""
        encoded = content.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        # Content-addressed names make retries and repeated context transforms
        # idempotent while retaining the call identifier in the metadata.
        filename = f"{_safe_name(name)}-{digest[:24]}.{extension}"
        path = self.root / filename
        relative = path.relative_to(self.workspace).as_posix()
        return ContextArtifact(
            tool_call_id=identifier,
            tool_name=name,
            path=relative,
            byte_size=len(encoded),
            sha256=digest,
            preview=self._preview(content),
            created_at=_now(),
        )

    def persist(self, artifact: ContextArtifact, content: str) -> None:
        path = (self.workspace / artifact.path).resolve()
        path.relative_to(self.root.resolve())
        encoded = content.encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != artifact.sha256:
            raise ValueError("artifact content differs from its prepared reference")
        if path.is_file() and path.read_bytes() == encoded:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)


def format_artifact_reference(artifact: ContextArtifact, include_preview: bool = True) -> str:
    lines = [
        ARTIFACT_MARKER,
        f"Tool: {artifact.tool_name}",
        f"Full result: {artifact.path}",
        f"UTF-8 bytes: {artifact.byte_size}",
        f"SHA-256: {artifact.sha256}",
    ]
    if include_preview:
        lines.extend(["", "Preview:", artifact.preview])
    return "\n".join(lines)
