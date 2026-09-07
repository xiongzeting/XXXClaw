from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from MiniClaw.cancellation import CancellationToken

from .atomic import atomic_write_bytes
from .base import ToolResult
from .mutation_queue import with_file_mutation_queue
from .workspace import WorkspaceGuard


@dataclass(slots=True)
class WriteTool:
    boundary: WorkspaceGuard

    name = "write"
    description = "Write UTF-8 content to a workspace file, creating parent directories and replacing existing content."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    async def execute(
        self,
        arguments: dict[str, Any],
        cancellation_token: CancellationToken | None = None,
    ) -> ToolResult:
        path = self.boundary.resolve(arguments["path"], access="write")
        content = arguments["content"].encode("utf-8")

        async def mutate() -> None:
            await atomic_write_bytes(path, content, cancellation_token)

        await with_file_mutation_queue(path, mutate)
        return ToolResult(
            content=f"Successfully wrote {len(content)} bytes to {arguments['path']}",
            details={"path": str(path), "bytes": len(content)},
        )
