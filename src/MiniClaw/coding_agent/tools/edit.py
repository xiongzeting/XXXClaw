from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from MiniClaw.cancellation import CancellationToken

from .atomic import atomic_write_bytes
from .base import ToolResult
from .edit_diff import (
    Edit,
    apply_edits_to_normalized_content,
    detect_line_ending,
    generate_diff_string,
    normalize_to_lf,
    restore_line_endings,
    strip_bom,
)
from .mutation_queue import with_file_mutation_queue
from .workspace import WorkspaceGuard


@dataclass(slots=True)
class EditTool:
    boundary: WorkspaceGuard

    name = "edit"
    description = (
        "Replace one or more unique text blocks in a workspace file. All edits match the original "
        "file and must not overlap. Preserves UTF-8 BOM and the file's CRLF/LF style."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "edits": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "oldText": {"type": "string"},
                        "newText": {"type": "string"},
                    },
                    "required": ["oldText", "newText"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["path", "edits"],
        "additionalProperties": False,
    }

    def prepare_arguments(self, input_arguments: dict[str, Any]) -> dict[str, Any]:
        arguments = dict(input_arguments)
        if isinstance(arguments.get("edits"), str):
            try:
                parsed = json.loads(arguments["edits"])
                if isinstance(parsed, list):
                    arguments["edits"] = parsed
            except json.JSONDecodeError:
                pass
        if isinstance(arguments.get("oldText"), str) and isinstance(arguments.get("newText"), str):
            edits = list(arguments.get("edits") or [])
            edits.append({"oldText": arguments.pop("oldText"), "newText": arguments.pop("newText")})
            arguments["edits"] = edits
        return arguments

    async def execute(
        self,
        arguments: dict[str, Any],
        cancellation_token: CancellationToken | None = None,
    ) -> ToolResult:
        path = self.boundary.resolve(arguments["path"], access="write", must_exist=True)
        if not path.is_file():
            raise IsADirectoryError(f"not a file: {arguments['path']}")
        edits = [Edit(item["oldText"], item["newText"]) for item in arguments["edits"]]
        async def mutate() -> tuple[str, int | None]:
            raw = (await asyncio.to_thread(path.read_bytes)).decode("utf-8", errors="strict")
            if cancellation_token is not None:
                cancellation_token.raise_if_tool_cancelled(stage="file_edit_prepare")
            bom, content = strip_bom(raw)
            ending = detect_line_ending(content)
            applied = apply_edits_to_normalized_content(normalize_to_lf(content), edits, arguments["path"])
            final_content = bom + restore_line_endings(applied.new_content, ending)
            encoded = final_content.encode("utf-8")
            await atomic_write_bytes(path, encoded, cancellation_token)
            return generate_diff_string(applied.base_content, applied.new_content)

        diff, first_changed_line = await with_file_mutation_queue(path, mutate)
        return ToolResult(
            content=f"Successfully replaced {len(edits)} block(s) in {arguments['path']}.",
            details={"path": str(path), "diff": diff, "firstChangedLine": first_changed_line},
        )
