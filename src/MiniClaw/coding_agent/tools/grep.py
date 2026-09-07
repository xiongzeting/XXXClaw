from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import ToolResult
from .truncate import DEFAULT_MAX_BYTES, GREP_MAX_LINE_LENGTH, format_size, truncate_head, truncate_line
from .workspace import WorkspaceGuard


DEFAULT_LIMIT = 100


@dataclass(slots=True)
class GrepTool:
    boundary: WorkspaceGuard

    name = "grep"
    description = (
        "Search workspace file contents with ripgrep. Supports regex/literal patterns, glob filters, "
        "case folding and context. Respects .gitignore and includes hidden files."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "glob": {"type": "string"},
            "ignoreCase": {"type": "boolean"},
            "literal": {"type": "boolean"},
            "context": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        rg = shutil.which("rg")
        if not rg:
            raise FileNotFoundError("ripgrep (rg) is required for the grep tool")
        search_path = self.boundary.resolve(
            arguments.get("path") or ".",
            access="search",
            must_exist=True,
        )
        is_directory = await asyncio.to_thread(search_path.is_dir)
        if not is_directory and not search_path.is_file():
            raise ValueError(f"Path is not searchable: {arguments.get('path', '.')}")

        limit = max(1, int(arguments.get("limit", DEFAULT_LIMIT)))
        context = max(0, int(arguments.get("context", 0)))
        command = [rg, "--json", "--line-number", "--color=never", "--hidden"]
        if arguments.get("ignoreCase"):
            command.append("--ignore-case")
        if arguments.get("literal"):
            command.append("--fixed-strings")
        if arguments.get("glob"):
            command.extend(["--glob", arguments["glob"]])
        for protected in self.boundary.protected_relative_paths(
            search_path,
            access="search",
        ):
            command.extend(["--glob", f"!{protected}"])
            command.extend(["--glob", f"!{protected}/**"])
        command.extend(["--", arguments["pattern"], str(search_path)])

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self.boundary.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        matches: list[tuple[Path, int, str | None]] = []
        limit_reached = False
        try:
            while line := await process.stdout.readline():
                try:
                    event = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if event.get("type") != "match":
                    continue
                data = event.get("data") or {}
                raw_path = ((data.get("path") or {}).get("text"))
                line_number = data.get("line_number")
                line_text = ((data.get("lines") or {}).get("text"))
                if not raw_path or not isinstance(line_number, int):
                    continue
                result_path = Path(raw_path)
                if not result_path.is_absolute():
                    result_path = self.boundary.root / result_path
                resolved_result = result_path.resolve()
                if self.boundary.is_protected(resolved_result, access="search"):
                    continue
                matches.append((resolved_result, line_number, line_text))
                if len(matches) >= limit:
                    limit_reached = True
                    process.kill()
                    break
            # Drain the unread stdout remainder as well as stderr. On Windows,
            # waiting on stderr alone can hang after rg is killed at the match
            # limit while JSON events remain buffered in stdout.
            _, stderr_bytes = await process.communicate()
            stderr = stderr_bytes.decode("utf-8", errors="replace")
        except asyncio.CancelledError:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise

        if not limit_reached and process.returncode not in (0, 1):
            raise RuntimeError(stderr.strip() or f"ripgrep exited with code {process.returncode}")
        if not matches:
            return ToolResult(
                content="No matches found",
                details={"matches": 0, "searchPath": str(search_path), "matchedPaths": []},
            )

        file_cache: dict[Path, list[str]] = {}
        output_lines: list[str] = []
        lines_truncated = False

        def display_path(path: Path) -> str:
            if is_directory:
                try:
                    return path.relative_to(search_path).as_posix()
                except ValueError:
                    pass
            return path.name

        for path, line_number, rg_line in matches:
            shown_path = display_path(path)
            if context == 0 and rg_line is not None:
                text = rg_line.replace("\r\n", "\n").replace("\r", "").removesuffix("\n")
                rendered, was_truncated = truncate_line(text)
                lines_truncated |= was_truncated
                output_lines.append(f"{shown_path}:{line_number}: {rendered}")
                continue
            lines = file_cache.get(path)
            if lines is None:
                try:
                    content = await asyncio.to_thread(
                        path.read_text,
                        encoding="utf-8",
                        errors="replace",
                    )
                    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                except OSError:
                    lines = []
                file_cache[path] = lines
            if not lines:
                output_lines.append(f"{shown_path}:{line_number}: (unable to read file)")
                continue
            start = max(1, line_number - context)
            end = min(len(lines), line_number + context)
            for current in range(start, end + 1):
                rendered, was_truncated = truncate_line(lines[current - 1])
                lines_truncated |= was_truncated
                separator = ":" if current == line_number else "-"
                output_lines.append(f"{shown_path}{separator}{current}{separator} {rendered}")

        truncation = truncate_head("\n".join(output_lines), max_lines=2**63 - 1)
        output = truncation.content
        notices: list[str] = []
        details: dict[str, Any] = {
            "matches": len(matches),
            "searchPath": str(search_path),
            "matchedPaths": list(dict.fromkeys(str(path) for path, _, _ in matches)),
        }
        if limit_reached:
            notices.append(f"{limit} matches limit reached. Use limit={limit * 2} for more, or refine pattern")
            details["matchLimitReached"] = limit
        if truncation.truncated:
            notices.append(f"{format_size(DEFAULT_MAX_BYTES)} limit reached")
            details["truncation"] = truncation.to_details()
        if lines_truncated:
            notices.append(f"Some lines truncated to {GREP_MAX_LINE_LENGTH} chars. Use read to see full lines")
            details["linesTruncated"] = True
        if notices:
            output += f"\n\n[{'. '.join(notices)}]"
        return ToolResult(content=output, details=details)
