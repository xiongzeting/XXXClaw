from __future__ import annotations

import asyncio
import fnmatch
import os
from dataclasses import dataclass
from typing import Any

from .base import ToolResult
from .workspace import WorkspaceGuard


@dataclass(slots=True)
class FindTool:
    boundary: WorkspaceGuard

    name = "find"
    description = "按文件名模式查找工作区路径；只返回路径，不读取文件内容，最多返回 500 项。"
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "type": {"type": "string", "enum": ["file", "directory", "any"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        pattern = str(arguments["pattern"]).strip()
        if not pattern or "\0" in pattern:
            raise ValueError("pattern must be non-empty and contain no NUL characters")
        root = self.boundary.resolve(arguments.get("path") or ".", access="search", must_exist=True)
        if not root.is_dir():
            raise NotADirectoryError(f"not a directory: {arguments.get('path') or '.'}")
        kind = str(arguments.get("type", "any"))
        limit = min(500, max(1, int(arguments.get("limit", 100))))
        matches = await asyncio.to_thread(self._find, root, pattern, kind, limit)
        return ToolResult(
            content="\n".join(matches) if matches else "No matches found",
            details={"pattern": pattern, "path": self.boundary.relative_path(root) or ".", "matches": len(matches), "limit": limit},
        )

    def _find(self, root, pattern: str, kind: str, limit: int) -> list[str]:
        matches: list[str] = []
        stack = [root]
        while stack and len(matches) < limit:
            directory = stack.pop()
            try:
                entries = sorted(os.scandir(directory), key=lambda item: item.name.lower(), reverse=True)
            except OSError:
                continue
            for entry in entries:
                if entry.is_symlink() or self.boundary.is_protected(entry.path, access="search"):
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                if fnmatch.fnmatchcase(entry.name, pattern) and (kind == "any" or (kind == "directory") == is_dir):
                    matches.append(self.boundary.relative_path(entry.path) + ("/" if is_dir else ""))
                    if len(matches) >= limit:
                        break
                if is_dir:
                    stack.append(entry.path)
        return sorted(matches)
