from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from MiniClaw.cancellation import CancellationToken


async def atomic_write_bytes(
    path: Path,
    content: bytes,
    cancellation_token: CancellationToken | None = None,
) -> None:
    """Stage bytes beside the target and commit only while the token is still active."""

    if cancellation_token is not None:
        cancellation_token.raise_if_tool_cancelled(stage="file_write_prepare")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.miniclaw-{uuid.uuid4().hex}.tmp")
    try:
        await asyncio.to_thread(temporary.write_bytes, content)
        if cancellation_token is not None:
            cancellation_token.raise_if_tool_cancelled(stage="file_write_commit")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
