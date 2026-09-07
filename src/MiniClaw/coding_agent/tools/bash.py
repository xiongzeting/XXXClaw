from __future__ import annotations

import asyncio
import hashlib
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from MiniClaw.cancellation import CancellationToken
from MiniClaw.coding_agent.runtime.execution import (
    CommandExecution,
    CommandExecutor,
    HostCommandExecutor,
)

from .base import ToolResult
from .truncate import DEFAULT_MAX_BYTES, format_size, truncate_tail
from .workspace import WorkspaceGuard


BashExecution = CommandExecution
BashOperations = CommandExecutor
LocalBashOperations = HostCommandExecutor


@dataclass(slots=True)
class BashTool:
    boundary: WorkspaceGuard
    operations: BashOperations | None = None

    name = "bash"
    _description = (
        "Run a shell command in the workspace. stdout and stderr are merged; the last 2000 lines "
        "or 50KB are returned. timeout is measured in seconds and cannot exceed 900. "
        "Return normal command output and errors; no special verification JSON is required."
    )

    @property
    def description(self) -> str:
        backend = getattr(getattr(self.operations, "settings", None), "backend", "host")
        if backend == "docker":
            environment = "Executes POSIX sh in the Linux container. "
        elif os.name == "nt":
            environment = (
                "Windows host: executes cmd.exe, NOT Bash or PowerShell. Do not use pwd, ls -la, "
                "/dev/null or semicolon-separated cmd commands. For PowerShell work, write a "
                ".ps1 file and wait for write success before executing it with the "
                "detected pwsh.exe -File. "
            )
        else:
            environment = "Executes /bin/sh on the host. "
        return environment + self._description
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {
                "type": "number",
                "minimum": 0.001,
                "maximum": 900,
                "description": "Command timeout in seconds (maximum 900).",
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    async def execute(
        self,
        arguments: dict[str, Any],
        cancellation_token: CancellationToken | None = None,
    ) -> ToolResult:
        timeout = float(arguments["timeout"]) if "timeout" in arguments else None
        before = await asyncio.to_thread(_snapshot_workspace, self.boundary.root)
        execution = await (self.operations or LocalBashOperations()).run(
            arguments["command"], self.boundary.root, timeout, cancellation_token
        )
        after = await asyncio.to_thread(_snapshot_workspace, self.boundary.root)
        separated = bool(execution.details.get("streams_separated"))
        stdout = execution.stdout if separated else execution.output
        stderr = execution.stderr if separated else b""
        full_output = execution.output.decode("utf-8", errors="replace")
        truncation = truncate_tail(full_output)
        details: dict[str, Any] = {
            "exit_code": execution.exit_code,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "workspace_changes": _workspace_changes(before, after),
            **execution.details,
        }
        output = truncation.content or "(no output)"
        if truncation.truncated:
            output_dir = self.boundary.internal_path(".aster/tool-output")
            await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
            output_path = output_dir / f"bash-{uuid.uuid4().hex}.log"
            await asyncio.to_thread(output_path.write_bytes, execution.output)
            relative_output = output_path.relative_to(self.boundary.root).as_posix()
            details.update({"truncation": truncation.to_details(), "fullOutputPath": relative_output})
            start_line = truncation.total_lines - truncation.output_lines + 1
            end_line = truncation.total_lines
            if truncation.last_line_partial:
                last_line_size = format_size(len(full_output.split("\n")[-1].encode("utf-8")))
                notice = (
                    f"[Showing last {format_size(truncation.output_bytes)} of line {end_line} "
                    f"(line is {last_line_size}). Full output: {relative_output}]"
                )
            elif truncation.truncated_by == "lines":
                notice = f"[Showing lines {start_line}-{end_line} of {truncation.total_lines}. Full output: {relative_output}]"
            else:
                notice = (
                    f"[Showing lines {start_line}-{end_line} of {truncation.total_lines} "
                    f"({format_size(DEFAULT_MAX_BYTES)} limit). Full output: {relative_output}]"
                )
            output = f"{output}\n\n{notice}"
        if execution.exit_code != 0:
            output = f"{output}\n\nCommand exited with code {execution.exit_code}"
        return ToolResult(content=output, is_error=execution.exit_code != 0, details=details)


_SNAPSHOT_MAX_ENTRIES = 20_000
_SNAPSHOT_MAX_BYTES = 64 * 1024 * 1024
_SNAPSHOT_MAX_FILE_BYTES = 8 * 1024 * 1024


def _snapshot_workspace(root) -> tuple[dict[str, tuple[int, int, str]], bool]:
    """Return a bounded content identity map and whether the scan was complete."""
    root = root.resolve()
    files: dict[str, tuple[int, int, str]] = {}
    complete = True
    count = 0
    total_bytes = 0
    deadline = time.monotonic() + 2.0

    def visit(directory):
        nonlocal complete, count, total_bytes
        try:
            with os.scandir(directory) as scan:
                entries = []
                for entry in scan:
                    count += 1
                    if count > _SNAPSHOT_MAX_ENTRIES or time.monotonic() > deadline:
                        complete = False
                        break
                    entries.append(entry)
        except OSError:
            complete = False
            return
        for entry in entries:
            if count > _SNAPSHOT_MAX_ENTRIES or time.monotonic() > deadline:
                complete = False
                return
            if entry.name in {".aster", ".git"} and directory == root:
                continue
            path = directory / entry.name
            try:
                if entry.is_symlink():
                    complete = False
                    continue
                resolved = path.resolve()
                if resolved != root and root not in resolved.parents:
                    complete = False
                    continue
                if entry.is_dir(follow_symlinks=False):
                    visit(path)
                elif entry.is_file(follow_symlinks=False):
                    stat = entry.stat(follow_symlinks=False)
                    total_bytes += stat.st_size
                    if stat.st_size > _SNAPSHOT_MAX_FILE_BYTES or total_bytes > _SNAPSHOT_MAX_BYTES:
                        complete = False
                        continue
                    digest_obj = hashlib.sha256()
                    with path.open("rb") as stream:
                        while chunk := stream.read(1024 * 1024):
                            if time.monotonic() > deadline:
                                complete = False
                                return
                            digest_obj.update(chunk)
                    digest = digest_obj.hexdigest()
                    files[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns, digest)
            except (OSError, ValueError):
                complete = False
        
    visit(root)
    return files, complete


def _workspace_changes(before, after) -> dict[str, Any]:
    before_files, before_complete = before
    after_files, after_complete = after
    unchanged = {
        path
        for path in set(before_files) & set(after_files)
        if before_files[path] == after_files[path]
    }
    changed = sorted((set(before_files) | set(after_files)) - unchanged)
    return {"complete": before_complete and after_complete, "changed_paths": changed}
