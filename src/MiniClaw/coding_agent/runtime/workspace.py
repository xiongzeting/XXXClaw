from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal


AccessMode = Literal["read", "write", "search", "execute"]

_ARTIFACT_READ_ROOTS = {
    (".aster", "tool-output"),
    (".aster", "context-artifacts"),
}
_SENSITIVE_DIRECTORIES = {".ssh", ".aws", ".azure", ".kube", ".gnupg", ".docker"}
_SENSITIVE_FILES = {
    "memory.md",
    "log.jsonl",
    "auth.json",
    "credentials",
    "credentials.json",
    "secrets",
    "secrets.json",
    ".npmrc",
    ".pypirc",
    ".netrc",
}


def is_sensitive_relative_path(relative: str | Path) -> bool:
    """Return whether a normalized workspace-relative path contains credentials."""

    path = Path(relative)
    parts = tuple(part.casefold() for part in path.parts if part not in {"", "."})
    if not parts:
        return False
    if any(part in _SENSITIVE_DIRECTORIES for part in parts):
        return True
    name = parts[-1]
    normalized = "/".join(parts)
    return bool(
        name == ".env"
        or name.startswith(".env.")
        or name in _SENSITIVE_FILES
        or name.endswith((".pem", ".key", ".p12", ".pfx"))
        or normalized.endswith("/.git/config")
        or normalized == ".git/config"
        or "/.git/credentials" in normalized
        or normalized.startswith(".git/credentials")
    )


def is_protected_relative_path(relative: str | Path, access: AccessMode) -> bool:
    """Canonical operation-aware policy shared by tools, approval and Docker masking."""

    path = Path(relative)
    parts = tuple(part.casefold() for part in path.parts if part not in {"", "."})
    if not parts:
        return False

    if ".aster" in parts:
        aster_index = parts.index(".aster")
        scoped = parts[aster_index:]
        if access == "read" and any(
            scoped[: len(prefix)] == prefix and len(scoped) >= len(prefix)
            for prefix in _ARTIFACT_READ_ROOTS
        ):
            return False
        return True

    if ".git" in parts:
        git_index = parts.index(".git")
        git_path = "/".join(parts[git_index:])
        if access in {"write", "search"}:
            return True
        if git_path == ".git/config" or git_path.startswith(".git/credentials"):
            return True

    return is_sensitive_relative_path(path)


@dataclass(slots=True, frozen=True)
class WorkspaceGuard:
    """Map model-visible paths to one host workspace and prevent path escape."""

    root: Path
    execution_root: str
    protected_paths: tuple[Path, ...]

    def __init__(
        self,
        root: str | Path,
        execution_root: str | None = None,
        protected_paths: list[str | Path] | tuple[str | Path, ...] = (),
    ) -> None:
        resolved_root = Path(root).resolve(strict=True)
        if not resolved_root.is_dir():
            raise NotADirectoryError(f"workspace is not a directory: {root}")
        object.__setattr__(self, "root", resolved_root)
        object.__setattr__(
            self,
            "execution_root",
            execution_root.rstrip("/") if execution_root else str(resolved_root),
        )
        protected = tuple(Path(path).resolve(strict=False) for path in protected_paths)
        object.__setattr__(self, "protected_paths", protected)

    def resolve(
        self,
        user_path: str | Path,
        *,
        access: AccessMode = "read",
        must_exist: bool = False,
    ) -> Path:
        resolved = self.normalize(user_path, must_exist=must_exist)
        self._assert_access_allowed(resolved, str(user_path), access)
        return resolved

    def normalize(self, user_path: str | Path, *, must_exist: bool = False) -> Path:
        """Resolve a model-visible path without applying per-operation policy."""

        raw = str(user_path)
        if not raw or "\0" in raw:
            raise ValueError("path must be a non-empty path without NUL characters")
        candidate = self._candidate(raw)
        try:
            resolved = candidate.resolve(strict=must_exist)
        except FileNotFoundError:
            raise
        self._assert_inside(resolved, raw)

        anchor = resolved
        while not anchor.exists() and anchor != self.root:
            anchor = anchor.parent
        actual_anchor = anchor.resolve(strict=True)
        self._assert_inside(actual_anchor, raw)
        return resolved

    def internal_path(self, relative_path: str | Path) -> Path:
        """Resolve a private Runtime-owned path without exposing it to tool ACLs."""

        raw = str(relative_path)
        path = Path(raw)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("internal path must be a normalized workspace-relative path")
        return self.normalize(path, must_exist=False)

    def to_execution_path(self, host_path: str | Path) -> str:
        resolved = Path(host_path).resolve(strict=False)
        self._assert_inside(resolved, str(host_path))
        relative = resolved.relative_to(self.root).as_posix()
        if self.execution_root.startswith("/"):
            root = PurePosixPath(self.execution_root)
            return str(root / relative) if relative else str(root)
        return str(resolved)

    def relative_path(self, host_path: str | Path) -> str:
        resolved = Path(host_path).resolve(strict=False)
        self._assert_inside(resolved, str(host_path))
        return resolved.relative_to(self.root).as_posix()

    def is_protected(
        self,
        host_path: str | Path,
        *,
        access: AccessMode = "read",
    ) -> bool:
        try:
            candidate = Path(host_path).resolve(strict=False)
            self._assert_inside(candidate, str(host_path))
            self._assert_access_allowed(candidate, str(host_path), access)
        except (OSError, PermissionError, ValueError):
            return True
        return False

    def protected_relative_paths(
        self,
        base: str | Path,
        *,
        access: AccessMode = "search",
    ) -> list[str]:
        root = Path(base).resolve(strict=False)
        values: list[str] = []
        for protected in self.protected_paths_for(access):
            try:
                values.append(protected.relative_to(root).as_posix())
            except ValueError:
                continue
        return values

    def protected_paths_for(self, access: AccessMode) -> tuple[Path, ...]:
        """Return current existing protected paths, including files created after startup."""

        protected = list(self.protected_paths)
        for directory, names, files in os.walk(self.root, followlinks=False):
            directory_path = Path(directory)
            kept_names: list[str] = []
            for name in names:
                path = directory_path / name
                relative = path.relative_to(self.root)
                if access == "read" and tuple(
                    part.casefold() for part in relative.parts
                ) == (".aster",):
                    kept_names.append(name)
                    continue
                if is_protected_relative_path(relative, access):
                    protected.append(path.resolve(strict=False))
                else:
                    kept_names.append(name)
            names[:] = kept_names
            for name in files:
                path = directory_path / name
                if is_protected_relative_path(path.relative_to(self.root), access):
                    protected.append(path.resolve(strict=False))
        protected.sort(key=lambda item: len(item.parts))
        unique: list[Path] = []
        for path in protected:
            if not any(_same_or_inside(parent, path) for parent in unique):
                unique.append(path)
        return tuple(unique)

    def _candidate(self, raw: str) -> Path:
        normalized = raw.replace("\\", "/")
        execution_root = self.execution_root.replace("\\", "/").rstrip("/")
        if execution_root.startswith("/") and (
            normalized == execution_root or normalized.startswith(f"{execution_root}/")
        ):
            relative = normalized[len(execution_root) :].lstrip("/")
            return self.root / Path(*PurePosixPath(relative).parts)
        path = Path(raw)
        return path if path.is_absolute() else self.root / path

    def _assert_inside(self, candidate: Path, original: str) -> None:
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(f"path is outside workspace: {original}") from exc

    def _assert_access_allowed(
        self,
        candidate: Path,
        original: str,
        access: AccessMode,
    ) -> None:
        for protected in self.protected_paths:
            if _same_or_inside(protected, candidate):
                raise PermissionError(f"path is protected from tool access: {original}")
        relative = candidate.relative_to(self.root)
        if is_protected_relative_path(relative, access):
            raise PermissionError(
                f"path is protected from tool {access} access: {original}"
            )


def _same_or_inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False
