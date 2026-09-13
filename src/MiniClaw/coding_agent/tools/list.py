from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from .base import ToolResult
from .workspace import WorkspaceGuard


@dataclass(slots=True)
class ListTool:
    """Small structured directory listing; bash remains available for richer cases."""

    boundary: WorkspaceGuard

    name = "ls"
    description = "列出工作区目录内容，只返回路径和目录标记，不读取文件；结果最多返回 500 项。"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        path = self.boundary.resolve(arguments.get("path") or ".", access="search", must_exist=True)
        if not path.is_dir():
            raise NotADirectoryError(f"not a directory: {arguments.get('path') or '.'}")
        limit = min(500, max(1, int(arguments.get("limit", 100))))
        rows = await asyncio.to_thread(self._list, path, limit)
        return ToolResult(
            content="\n".join(rows) if rows else "(empty directory)",
            details={"path": self.boundary.relative_path(path) or ".", "entries": len(rows), "limit": limit},
        )

    def _list(self, path, limit: int) -> list[str]:
        entries: list[str] = []
        with os.scandir(path) as scan:
            for entry in sorted(scan, key=lambda item: (not item.is_dir(follow_symlinks=False), item.name.lower())):
                if entry.is_symlink() or self.boundary.is_protected(entry.path, access="search"):
                    continue
                suffix = "/" if entry.is_dir(follow_symlinks=False) else ""
                entries.append(f"{self.boundary.relative_path(entry.path)}{suffix}")
                if len(entries) >= limit:
                    break
        return entries
