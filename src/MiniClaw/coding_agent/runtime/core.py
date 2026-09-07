from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from MiniClaw.cancellation import CancellationToken

from .config import RuntimeSettings
from .execution import CommandExecution, DockerCommandExecutor, HostCommandExecutor
from .snapshot import SnapshotManifest, prepare_snapshot_workspace
from .workspace import WorkspaceGuard


@dataclass(slots=True)
class ToolRuntime:
    """Session-scoped bridge between tools and the host or Docker executor."""

    settings: RuntimeSettings
    source_workspace: Path
    host_workspace: Path
    execution_workspace: str
    session_dir: Path
    task_id: str
    workspace: WorkspaceGuard
    command_executor: HostCommandExecutor | DockerCommandExecutor
    snapshot_manifest: SnapshotManifest | None = None

    async def run(
        self,
        command: str,
        cwd: Path,
        timeout: float | None,
        cancellation_token: CancellationToken | None = None,
    ) -> CommandExecution:
        effective_timeout = (
            self.settings.default_command_timeout_seconds if timeout is None else float(timeout)
        )
        if effective_timeout > self.settings.max_command_timeout_seconds:
            raise ValueError(
                "Command timeout exceeds "
                f"{self.settings.max_command_timeout_seconds:g} seconds"
            )
        return await self.command_executor.run(
            command,
            cwd,
            effective_timeout,
            cancellation_token,
        )

    def trace_metadata(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "backend": self.settings.backend,
            "workspace_mode": self.settings.workspace_mode,
            "source_workspace": str(self.source_workspace),
            "host_workspace": str(self.host_workspace),
            "execution_workspace": self.execution_workspace,
            "default_timeout_seconds": self.settings.default_command_timeout_seconds,
            "max_timeout_seconds": self.settings.max_command_timeout_seconds,
        }
        if self.settings.backend == "docker":
            data["docker"] = {
                "image": self.settings.docker_image,
                "network": self.settings.docker_network,
                "cpus": self.settings.docker_cpus,
                "memory_mb": self.settings.docker_memory_mb,
                "pids_limit": self.settings.docker_pids_limit,
                "tmpfs_mb": self.settings.docker_tmpfs_mb,
            }
        if self.snapshot_manifest:
            data["snapshot"] = {
                "copied_files": self.snapshot_manifest.copied_files,
                "copied_bytes": self.snapshot_manifest.copied_bytes,
                "excluded_entries": self.snapshot_manifest.excluded_entries,
            }
        return data


def create_tool_runtime(
    source_workspace: str | Path,
    session_dir: str | Path,
    task_id: str,
    settings: RuntimeSettings,
    *,
    validate_docker: bool = True,
) -> ToolRuntime:
    source = Path(source_workspace).resolve(strict=True)
    session = Path(session_dir).resolve(strict=False)
    session.mkdir(parents=True, exist_ok=True)
    snapshot_manifest = None
    if settings.workspace_mode == "snapshot":
        host_workspace = session / "sandbox" / "workspace"
        snapshot_manifest = prepare_snapshot_workspace(
            source, host_workspace, session / "sandbox" / "manifest.json", task_id, settings
        )
    else:
        host_workspace = source
    execution_workspace = "/workspace" if settings.backend == "docker" else str(host_workspace)
    guard = WorkspaceGuard(
        host_workspace,
        execution_root=execution_workspace,
    )
    if settings.backend == "docker":
        executor = DockerCommandExecutor(settings, guard, session, task_id)
        if validate_docker:
            executor.validate()
    else:
        executor = HostCommandExecutor()
    return ToolRuntime(
        settings=settings,
        source_workspace=source,
        host_workspace=host_workspace.resolve(strict=True),
        execution_workspace=execution_workspace,
        session_dir=session,
        task_id=task_id,
        workspace=guard,
        command_executor=executor,
        snapshot_manifest=snapshot_manifest,
    )
