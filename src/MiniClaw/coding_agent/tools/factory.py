from __future__ import annotations

from pathlib import Path

from MiniClaw.coding_agent.runtime.core import ToolRuntime

from .base import Tool
from .bash import BashTool
from .edit import EditTool
from .grep import GrepTool
from .search import SearchTool
from .read import ReadTool
from .workspace import WorkspaceGuard
from .write import WriteTool


def create_coding_tools(workspace: str | Path | ToolRuntime) -> list[Tool]:
    runtime = workspace if isinstance(workspace, ToolRuntime) else None
    boundary = runtime.workspace if runtime else WorkspaceGuard(workspace)
    return [
        ReadTool(boundary),
        BashTool(boundary, operations=runtime),
        EditTool(boundary),
        WriteTool(boundary),
        GrepTool(boundary),
        SearchTool(boundary),
    ]
