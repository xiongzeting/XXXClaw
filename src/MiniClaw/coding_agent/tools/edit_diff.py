from __future__ import annotations

"""Text matching and diff helpers ported from pi's edit tool."""

import difflib
import unicodedata
from dataclasses import dataclass


SMART_SINGLE_QUOTES = str.maketrans("\u2018\u2019\u201a\u201b", "''''")
SMART_DOUBLE_QUOTES = str.maketrans("\u201c\u201d\u201e\u201f", '\"\"\"\"')
UNICODE_DASHES = str.maketrans({ord(char): "-" for char in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"})
SPECIAL_SPACES = str.maketrans({ord(char): " " for char in "\u00a0\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000"})


@dataclass(slots=True, frozen=True)
class Edit:
    old_text: str
    new_text: str


@dataclass(slots=True, frozen=True)
class AppliedEdits:
    base_content: str
    new_content: str


def detect_line_ending(content: str) -> str:
    crlf_index = content.find("\r\n")
    lf_index = content.find("\n")
    if lf_index == -1 or crlf_index == -1:
        return "\n"
    return "\r\n" if crlf_index < lf_index else "\n"


def normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def restore_line_endings(text: str, ending: str) -> str:
    return text.replace("\n", "\r\n") if ending == "\r\n" else text


def strip_bom(content: str) -> tuple[str, str]:
    return ("\ufeff", content[1:]) if content.startswith("\ufeff") else ("", content)


def normalize_for_fuzzy_match(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    return (
        normalized.translate(SMART_SINGLE_QUOTES)
        .translate(SMART_DOUBLE_QUOTES)
        .translate(UNICODE_DASHES)
        .translate(SPECIAL_SPACES)
    )


def _find(content: str, old_text: str) -> tuple[int, int, bool]:
    exact = content.find(old_text)
    if exact >= 0:
        return exact, len(old_text), False
    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old = normalize_for_fuzzy_match(old_text)
    fuzzy = fuzzy_content.find(fuzzy_old)
    return (fuzzy, len(fuzzy_old), True) if fuzzy >= 0 else (-1, 0, False)


def _label(index: int, total: int) -> str:
    return "oldText" if total == 1 else f"edits[{index}].oldText"


def apply_edits_to_normalized_content(content: str, edits: list[Edit], path: str) -> AppliedEdits:
    normalized_edits = [Edit(normalize_to_lf(e.old_text), normalize_to_lf(e.new_text)) for e in edits]
    for index, edit in enumerate(normalized_edits):
        if not edit.old_text:
            raise ValueError(f"{_label(index, len(edits))} must not be empty in {path}.")

    initial = [_find(content, edit.old_text) for edit in normalized_edits]
    base_content = normalize_for_fuzzy_match(content) if any(match[2] for match in initial) else content

    matches: list[tuple[int, int, int, str]] = []
    for index, edit in enumerate(normalized_edits):
        match_index, match_length, _ = _find(base_content, edit.old_text)
        fuzzy_old = normalize_for_fuzzy_match(edit.old_text)
        occurrences = normalize_for_fuzzy_match(base_content).count(fuzzy_old)
        if match_index < 0:
            target = "the exact text" if len(edits) == 1 else f"edits[{index}]"
            raise ValueError(
                f"Could not find {target} in {path}. The old text must match exactly including all whitespace and newlines."
            )
        if occurrences > 1:
            target = "the text" if len(edits) == 1 else f"edits[{index}]"
            raise ValueError(
                f"Found {occurrences} occurrences of {target} in {path}. Each oldText must be unique; provide more context."
            )
        matches.append((match_index, match_length, index, edit.new_text))

    matches.sort(key=lambda item: item[0])
    for previous, current in zip(matches, matches[1:]):
        if previous[0] + previous[1] > current[0]:
            raise ValueError(
                f"edits[{previous[2]}] and edits[{current[2]}] overlap in {path}. "
                "Merge them into one edit or target disjoint regions."
            )

    updated = base_content
    for match_index, match_length, _, new_text in reversed(matches):
        updated = updated[:match_index] + new_text + updated[match_index + match_length :]
    if updated == base_content:
        raise ValueError(f"No changes made to {path}. The replacement produced identical content.")
    return AppliedEdits(base_content, updated)


def generate_diff_string(old_content: str, new_content: str, context_lines: int = 4) -> tuple[str, int | None]:
    old_lines = old_content.split("\n")
    new_lines = new_content.split("\n")
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    width = len(str(max(len(old_lines), len(new_lines))))
    output: list[str] = []
    first_changed_line: int | None = None
    for group in matcher.get_grouped_opcodes(context_lines):
        if output:
            output.append(f" {' ' * width} ...")
        for tag, old_start, old_end, new_start, new_end in group:
            if tag == "equal":
                for offset, line in enumerate(old_lines[old_start:old_end]):
                    output.append(f" {old_start + offset + 1:>{width}} {line}")
                continue
            if first_changed_line is None:
                first_changed_line = new_start + 1
            if tag in {"replace", "delete"}:
                for offset, line in enumerate(old_lines[old_start:old_end]):
                    output.append(f"-{old_start + offset + 1:>{width}} {line}")
            if tag in {"replace", "insert"}:
                for offset, line in enumerate(new_lines[new_start:new_end]):
                    output.append(f"+{new_start + offset + 1:>{width}} {line}")
    return "\n".join(output), first_changed_line
