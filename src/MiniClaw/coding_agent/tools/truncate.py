from __future__ import annotations

"""Shared output truncation rules ported from pi's coding-agent tools."""

from dataclasses import asdict, dataclass
from typing import Literal


DEFAULT_MAX_LINES = 2_000
DEFAULT_MAX_BYTES = 50 * 1024
GREP_MAX_LINE_LENGTH = 500


@dataclass(slots=True, frozen=True)
class TruncationResult:
    content: str
    truncated: bool
    truncated_by: Literal["lines", "bytes"] | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    last_line_partial: bool
    first_line_exceeds_limit: bool
    max_lines: int
    max_bytes: int

    def to_details(self) -> dict[str, object]:
        return asdict(self)


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def _byte_length(text: str) -> int:
    return len(text.encode("utf-8"))


def truncate_head(
    content: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    lines = content.split("\n")
    total_lines = len(lines)
    total_bytes = _byte_length(content)
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return TruncationResult(
            content, False, None, total_lines, total_bytes, total_lines, total_bytes,
            False, False, max_lines, max_bytes,
        )

    first_line_bytes = _byte_length(lines[0])
    if first_line_bytes > max_bytes:
        return TruncationResult(
            "", True, "bytes", total_lines, total_bytes, 0, 0,
            False, True, max_lines, max_bytes,
        )

    output: list[str] = []
    output_bytes = 0
    truncated_by: Literal["lines", "bytes"] = "lines"
    for index, line in enumerate(lines[:max_lines]):
        line_bytes = _byte_length(line) + (1 if index > 0 else 0)
        if output_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        output.append(line)
        output_bytes += line_bytes

    if len(output) >= max_lines and output_bytes <= max_bytes:
        truncated_by = "lines"
    result = "\n".join(output)
    return TruncationResult(
        result, True, truncated_by, total_lines, total_bytes, len(output),
        _byte_length(result), False, False, max_lines, max_bytes,
    )


def _truncate_utf8_from_end(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    start = len(encoded) - max_bytes
    while start < len(encoded) and encoded[start] & 0xC0 == 0x80:
        start += 1
    return encoded[start:].decode("utf-8")


def truncate_tail(
    content: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    lines = content.split("\n")
    total_lines = len(lines)
    total_bytes = _byte_length(content)
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return TruncationResult(
            content, False, None, total_lines, total_bytes, total_lines, total_bytes,
            False, False, max_lines, max_bytes,
        )

    output: list[str] = []
    output_bytes = 0
    truncated_by: Literal["lines", "bytes"] = "lines"
    last_line_partial = False
    for line in reversed(lines):
        if len(output) >= max_lines:
            break
        line_bytes = _byte_length(line) + (1 if output else 0)
        if output_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            if not output:
                output.insert(0, _truncate_utf8_from_end(line, max_bytes))
                output_bytes = _byte_length(output[0])
                last_line_partial = True
            break
        output.insert(0, line)
        output_bytes += line_bytes

    if len(output) >= max_lines and output_bytes <= max_bytes:
        truncated_by = "lines"
    result = "\n".join(output)
    return TruncationResult(
        result, True, truncated_by, total_lines, total_bytes, len(output),
        _byte_length(result), last_line_partial, False, max_lines, max_bytes,
    )


def truncate_line(line: str, max_chars: int = GREP_MAX_LINE_LENGTH) -> tuple[str, bool]:
    if len(line) <= max_chars:
        return line, False
    return f"{line[:max_chars]}... [truncated]", True
