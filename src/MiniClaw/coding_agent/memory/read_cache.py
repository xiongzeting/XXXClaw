"""Bounded, session-local read evidence retained across semantic compaction.

This never skips execution of a requested read. Replayed results retain their
tool role, and are reused only after hashing the current workspace file.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from dataclasses import asdict

from MiniClaw.llm.types import ChatMessage, ToolInvocation


class ReadSnapshotCache:
    def __init__(self, workspace: Path, budget_bytes: int, max_entries: int = 5):
        self.workspace = workspace.resolve()
        self.budget_bytes = budget_bytes
        self.max_entries = max(1, int(max_entries))
        self.entries: dict[str, dict] = {}
        # A compacted read is replayed once so the next request can continue
        # from the last known file contents.  It is not injected on every
        # request; the raw session and the artifact remain the source of truth.
        self._replayed: dict[str, str] = {}

    def restore(self, entry: dict) -> None:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            return
        self.entries.pop(entry["path"], None)
        self.entries[entry["path"]] = entry
        self._replayed.pop(entry["path"], None)
        while len(self.entries) > self.max_entries:
            self.entries.pop(next(iter(self.entries)))

    def reset_replay(self) -> None:
        """Allow one fresh replay after the active conversation is compacted."""
        self._replayed.clear()

    def capture_paths(self, paths: list[str]) -> list[dict]:
        """Capture a few key files for the first request after compaction.

        This is a bounded recovery snapshot, not a new model-visible tool
        call. Hashes are checked again when the snapshot is projected.
        """
        captured: list[dict] = []
        for raw in paths:
            if len(captured) >= self.max_entries:
                break
            try:
                path = (self.workspace / raw).resolve()
                relative = path.relative_to(self.workspace).as_posix()
                if relative.startswith(".aster/") or not path.is_file():
                    continue
                data = path.read_bytes()
                if len(data) > min(self.budget_bytes, 1024 * 1024):
                    continue
                content = data.decode("utf-8", errors="replace")
                digest = hashlib.sha256(data).hexdigest()
                call = ToolInvocation(
                    f"recovery-read-{uuid.uuid4().hex[:12]}",
                    "read",
                    {"path": relative},
                )
                entry = {
                    "path": relative,
                    "sha256": digest,
                    "call": asdict(call),
                    "content": content,
                    "recovery": True,
                }
                self.restore(entry)
                captured.append({"path": relative, "sha256": digest, "bytes": len(data)})
            except (OSError, ValueError):
                continue
        return captured

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
            if self._replayed.get(relative) == item["sha256"]:
                continue
            call = ToolInvocation(**item["call"])
            replay.extend([ChatMessage(role="assistant", tool_calls=[call]),
                           ChatMessage(role="tool", name="read", tool_call_id=call.call_id, content=content)])
            budget -= size
            reused.append({"path": relative, "sha256": item["sha256"], "bytes": size})
            self._replayed[relative] = item["sha256"]
        for relative in invalidated:
            self.entries.pop(relative, None)
            self._replayed.pop(relative, None)
        # Keep the compacted checkpoint first. Replayed read evidence is a
        # recovery layer between the checkpoint/manifest and recent original.
        if replay and messages:
            projected = [messages[0], *replay, *messages[1:]]
        else:
            projected = [*replay, *messages]
        return projected, {"read_snapshots": reused, "invalidated_read_paths": invalidated,
                                    "snapshot_bytes": self.budget_bytes - budget,
                                    "read_replay_skipped": sum(
                                        1 for relative, item in self.entries.items()
                                        if self._replayed.get(relative) == item.get("sha256")
                                    )}
