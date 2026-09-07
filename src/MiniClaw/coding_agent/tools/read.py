from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import ToolResult
from .truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, format_size, truncate_head
from .workspace import WorkspaceGuard


@dataclass(slots=True)
class ReadTool:
    boundary: WorkspaceGuard

    name = "read"
    description = (
        "Read a workspace file. Text output keeps the first 2000 lines or 50KB, whichever "
        "comes first. Use offset and limit to continue through large files."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative or in-workspace absolute path"},
            "offset": {"type": "integer", "minimum": 1, "description": "First line, 1-indexed"},
            "limit": {"type": "integer", "minimum": 1, "description": "Maximum lines to read"},
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        path = self.boundary.resolve(arguments["path"], access="read", must_exist=True)
        if not path.is_file():
            raise IsADirectoryError(f"not a file: {arguments['path']}")
        raw = await asyncio.to_thread(path.read_bytes)
        mime_type, _ = mimetypes.guess_type(path.name)
        supported_image_types = {"image/jpeg", "image/png", "image/gif", "image/webp"}
        if mime_type in supported_image_types:
            return ToolResult(
                content=(
                    f"Read image file [{mime_type}]\n"
                    "[Image attachments are not supported by this Python ToolResult yet.]"
                ),
                details={"path": str(path), "mime_type": mime_type, "image": True},
            )

        text = raw.decode("utf-8", errors="replace")
        all_lines = text.split("\n")
        offset = int(arguments.get("offset", 1))
        start = max(0, offset - 1)
        if start >= len(all_lines):
            raise ValueError(f"Offset {offset} is beyond end of file ({len(all_lines)} lines total)")

        limit = arguments.get("limit")
        selected_lines = all_lines[start:] if limit is None else all_lines[start : start + int(limit)]
        selected = "\n".join(selected_lines)
        truncation = truncate_head(selected)
        start_display = start + 1
        details: dict[str, Any] = {"path": str(path), "file_sha256": hashlib.sha256(raw).hexdigest()}

        if truncation.first_line_exceeds_limit:
            size = format_size(len(all_lines[start].encode("utf-8")))
            output = (
                f"[Line {start_display} is {size}, exceeds {format_size(DEFAULT_MAX_BYTES)} limit. "
                f"Use bash to inspect a bounded byte range of {arguments['path']}.]"
            )
            details["truncation"] = truncation.to_details()
        elif truncation.truncated:
            end_display = start_display + truncation.output_lines - 1
            next_offset = end_display + 1
            output = truncation.content
            if truncation.truncated_by == "lines":
                notice = (
                    f"[Showing lines {start_display}-{end_display} of {len(all_lines)}. "
                    f"Use offset={next_offset} to continue.]"
                )
            else:
                notice = (
                    f"[Showing lines {start_display}-{end_display} of {len(all_lines)} "
                    f"({format_size(DEFAULT_MAX_BYTES)} limit). Use offset={next_offset} to continue.]"
                )
            output = f"{output}\n\n{notice}"
            details["truncation"] = truncation.to_details()
        elif limit is not None and start + len(selected_lines) < len(all_lines):
            remaining = len(all_lines) - start - len(selected_lines)
            next_offset = start + len(selected_lines) + 1
            output = f"{truncation.content}\n\n[{remaining} more lines in file. Use offset={next_offset} to continue.]"
        else:
            output = truncation.content

        details.update({"offset": start_display, "output_lines": truncation.output_lines})
        return ToolResult(content=output, details=details)
