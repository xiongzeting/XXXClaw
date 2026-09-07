from __future__ import annotations

import asyncio
import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import ToolResult
from .workspace import WorkspaceGuard
from MiniClaw.coding_agent.runtime.workspace import is_protected_relative_path


DEFAULT_SEARCH_LIMIT = 1_000
MAX_SEARCH_LIMIT = 10_000


@dataclass(slots=True)
class SearchTool:
    """List workspace paths without requiring shell access."""

    boundary: WorkspaceGuard

    name = "search"
    description = (
        "List workspace files matching a glob pattern. Protected paths are excluded. "
        "Use path to select a search root and includeDirectories to include matching directories."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob such as **/*.py or src/**"},
            "path": {"type": "string", "description": "Workspace-relative search root"},
            "includeDirectories": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_SEARCH_LIMIT},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        pattern = str(arguments["pattern"]).replace("\\", "/").strip()
        if not pattern or "\0" in pattern:
            raise ValueError("pattern must be non-empty and contain no NUL characters")
        root = self.boundary.resolve(
            str(arguments.get("path") or "."),
            access="search",
            must_exist=True,
        )
        if not root.is_dir():
            raise NotADirectoryError(f"search path is not a directory: {arguments.get('path') or '.'}")
        limit = min(MAX_SEARCH_LIMIT, int(arguments.get("limit", DEFAULT_SEARCH_LIMIT)))
        include_directories = bool(arguments.get("includeDirectories", False))
        matches = await asyncio.to_thread(
            self._scan,
            root,
            pattern,
            include_directories,
            limit,
        )
        truncated = len(matches) > limit
        visible = matches[:limit]
        content = "\n".join(visible) if visible else "(no matches)"
        if truncated:
            content += f"\n\n[Showing first {limit} matches. Increase limit to continue.]"
        return ToolResult(
            content=content,
            details={
                "pattern": pattern,
                "path": self.boundary.relative_path(root) or ".",
                "matches": len(visible),
                "limit": limit,
                "truncated": truncated,
                "matchedPaths": visible,
            },
        )

    def _scan(
        self,
        root: Path,
        pattern: str,
        include_directories: bool,
        limit: int,
    ) -> list[str]:
        # Slash-containing patterns are workspace-relative. Bare names remain
        # recursive basename searches, as required by _matches below.
        if "/" in pattern:
            parts = pattern.split("/")
            # These forms cannot match normalized workspace-relative output.
            if any(part in {"", ".", ".."} for part in parts):
                return []
            literal_parts: list[str] = []
            for part in parts:
                if any(char in part for char in "*?["):
                    break
                literal_parts.append(part)
            if literal_parts:
                target = self._literal_target(literal_parts)
                if target is None:
                    return []
                if len(literal_parts) == len(parts):
                    # os.walk never reports the search root itself.
                    if target == root or not target.is_relative_to(root):
                        return []
                    relative = self.boundary.relative_path(target)
                    if target.is_file():
                        return [relative]
                    if include_directories and target.is_dir():
                        return [relative + "/"]
                    return []
                if not target.is_dir():
                    return []
                if target.is_relative_to(root):
                    root = target
                elif not root.is_relative_to(target):
                    return []

        return self._scan_tree(root, pattern, include_directories, limit)

    def _scan_tree(self, root: Path, pattern: str, include_directories: bool, limit: int) -> list[str]:
        # scandir caches entry types on Windows. Avoid stat/resolve calls for
        # every unrelated path; canonical ACL checks remain on all results.
        relative_root = root.relative_to(self.boundary.root).as_posix()
        stack = [(str(root), "" if relative_root == "." else relative_root + "/")]
        protected = tuple(os.path.normcase(str(path)) for path in self.boundary.protected_paths)
        matches: list[str] = []
        while stack:
            directory, prefix = stack.pop()
            try:
                with os.scandir(directory) as iterator:
                    entries = sorted(iterator, key=lambda entry: entry.name)
            except OSError:
                continue
            directories: list[tuple[str, str]] = []
            files: list[tuple[os.DirEntry, str]] = []
            for entry in entries:
                relative = prefix + entry.name
                try:
                    if entry.is_symlink():
                        continue
                    if not entry.is_dir(follow_symlinks=False):
                        if _matches(relative, pattern):
                            files.append((entry, relative))
                        continue
                    # No reparse-point traversal: lexical ancestor checks are
                    # valid only while every traversed directory is physical.
                    if _is_junction(entry) or is_protected_relative_path(relative, "search"):
                        continue
                    normalized = os.path.normcase(entry.path)
                    if any(normalized == item or normalized.startswith(item + os.sep) for item in protected):
                        continue
                    directories.append((entry.path, relative + "/"))
                    if include_directories and _matches(relative, pattern):
                        if not self.boundary.is_protected(entry.path, access="search"):
                            matches.append(self.boundary.relative_path(entry.path) + "/")
                            if len(matches) > limit:
                                return matches
                except OSError:
                    continue
            for entry, relative in files:
                if self.boundary.is_protected(entry.path, access="search"):
                    continue
                canonical = self.boundary.relative_path(entry.path)
                if _matches(canonical, pattern):
                    matches.append(canonical)
                    if len(matches) > limit:
                        return matches
            # Keep the original deterministic files-before-subdirectories order.
            stack.extend(reversed(directories))
        return matches

    def _literal_target(self, parts: list[str]) -> Path | None:
        """Resolve only the literal prefix, preserving case-sensitive matching.

        Check each component so optimized lookups cannot bypass protected
        ancestors or traverse symlinks that the recursive scanner skips.
        """
        target = self.boundary.root
        for part in parts:
            try:
                with os.scandir(target) as entries:
                    entry = next((item for item in entries if item.name == part), None)
                    if entry is None or entry.is_symlink() or _is_junction(entry):
                        return None
                    target = Path(entry.path)
                if self.boundary.is_protected(target, access="search"):
                    return None
            except OSError:
                return None
        return target


def _is_junction(entry: os.DirEntry) -> bool:
    if os.name != "nt":
        return False
    check = getattr(entry, "is_junction", None)
    if check is not None:
        return check()
    # Python before 3.12 has no DirEntry.is_junction.
    return bool(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400)


def _matches(relative: str, pattern: str) -> bool:
    normalized = relative.replace("\\", "/")
    if fnmatch.fnmatchcase(normalized, pattern):
        return True
    if pattern.startswith("**/"):
        return fnmatch.fnmatchcase(normalized, pattern[3:])
    if "/" not in pattern:
        return fnmatch.fnmatchcase(Path(normalized).name, pattern)
    return False
