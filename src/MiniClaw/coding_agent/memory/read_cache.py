"""Bounded, session-local read evidence retained across semantic compaction.

This never skips execution of a requested read. Replayed results retain their
tool role, and are reused only after hashing the current workspace file.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from dataclasses import asdict

from MiniClaw.llm.types import ChatMessage, ToolInvocation


class ReadSnapshotCache:
    def __init__(self, workspace: Path, budget_bytes: int):
        self.workspace = workspace.resolve()
        self.budget_bytes = budget_bytes
        self.entries: dict[str, dict] = {}

    def restore(self, entry: dict) -> None:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            return
        self.entries.pop(entry["path"], None)
        self.entries[entry["path"]] = entry
        while len(self.entries) > 16:
            self.entries.pop(next(iter(self.entries)))

    def observe(self, call, result) -> dict | None:
        if call.name != "read" or result.is_error or result.details.get("image"):
            return None
        try:
            path = Path(result.details["path"]).resolve()
            relative = path.relative_to(self.workspace).as_posix()
            self.entries.pop(relative, None)
            if relative.startswith(".aster/") or len(result.content.encode("utf-8")) > self.budget_bytes:
                return {"path": relative, "evicted": True}
            digest = result.details["file_sha256"]
            entry = {"path": relative, "sha256": digest, "call": asdict(call), "content": result.content}
            self.restore(entry)
            return entry
        except (KeyError, ValueError, OSError):
            return None

    def project(self, messages: list[ChatMessage]) -> tuple[list[ChatMessage], dict]:
        present = {call.call_id for m in messages for call in m.tool_calls}
        budget = self.budget_bytes
        replay = []
        reused = []
        invalidated = []
        for relative, item in reversed(list(self.entries.items())):
            if item.get("evicted") or item["call"]["call_id"] in present:
                continue
            content = item["content"]
            size = len(content.encode("utf-8"))
            if size > budget:
                continue
            try:
                path = (self.workspace / relative).resolve()
                path.relative_to(self.workspace)
                # Avoid unbounded file I/O in the request path.
                if path.stat().st_size > 1024 * 1024:
                    continue
                if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                    invalidated.append(relative)
                    continue
            except (OSError, ValueError):
                invalidated.append(relative)
                continue
            call = ToolInvocation(**item["call"])
            replay.extend([ChatMessage(role="assistant", tool_calls=[call]),
                           ChatMessage(role="tool", name="read", tool_call_id=call.call_id, content=content)])
            budget -= size
            reused.append({"path": relative, "sha256": item["sha256"], "bytes": size})
        for relative in invalidated:
            self.entries.pop(relative, None)
        return [*replay, *messages], {"read_snapshots": reused, "invalidated_read_paths": invalidated,
                                    "snapshot_bytes": self.budget_bytes - budget}
