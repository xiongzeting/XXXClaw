from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import shutil
import signal
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from MiniClaw.cancellation import CancellationToken, ToolCancelledError

from .config import RuntimeSettings
from .workspace import WorkspaceGuard

VERIFICATION_HELPERS = Path(__file__).parent / 'helpers'


@dataclass(slots=True, frozen=True)
class CommandExecution:
    output: bytes
    exit_code: int
    details: dict[str, Any] = field(default_factory=dict)
    stdout: bytes = b""
    stderr: bytes = b""


class CommandExecutor(Protocol):
    async def run(
        self,
        command: str,
        cwd: Path,
        timeout: float | None,
        cancellation_token: CancellationToken | None = None,
    ) -> CommandExecution: ...


async def terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(killer.wait(), timeout=10)
        except asyncio.TimeoutError:
            killer.kill()
            await killer.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    with contextlib.suppress(ProcessLookupError, asyncio.TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=10)


class HostCommandExecutor:
    async def run(
        self,
        command: str,
        cwd: Path,
        timeout: float | None,
        cancellation_token: CancellationToken | None = None,
    ) -> CommandExecution:
        if cancellation_token is not None:
            cancellation_token.raise_if_tool_cancelled(stage="host_process_start")
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
        try:
            stdout, stderr = await _await_runtime_operation(
                process.communicate(),
                cancellation_token,
                timeout=timeout,
                stage="host_process",
            )
        except asyncio.TimeoutError:
            await _cleanup(terminate_process_tree(process))
            raise TimeoutError(f"Command timed out after {timeout:g} seconds")
        except ToolCancelledError as exc:
            await _cleanup(terminate_process_tree(process))
            exc.details.update({"runtime": "host", "process_id": process.pid})
            raise
        except asyncio.CancelledError:
            await _cleanup(terminate_process_tree(process))
            raise
        return CommandExecution(
            output=stdout + stderr,
            exit_code=process.returncode or 0,
            details={"runtime": "host", "streams_separated": True},
            stdout=stdout,
            stderr=stderr,
        )


class DockerCommandExecutor:
    def __init__(
        self,
        settings: RuntimeSettings,
        workspace: WorkspaceGuard,
        session_dir: Path,
        task_id: str,
    ) -> None:
        self.settings = settings
        self.workspace = workspace
        self.session_dir = session_dir
        self.task_hash = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:12]
        self.docker = shutil.which("docker") or "docker"

    def validate(self) -> None:
        validate_docker_environment(self.settings, self.docker)

    async def run(
        self,
        command: str,
        cwd: Path,
        timeout: float | None,
        cancellation_token: CancellationToken | None = None,
    ) -> CommandExecution:
        if cwd.resolve() != self.workspace.root:
            raise PermissionError("Docker command cwd must be the runtime workspace root")
        if cancellation_token is not None:
            cancellation_token.raise_if_tool_cancelled(stage="docker_container_start")
        container_name = f"miniclaw-{self.task_hash}-{uuid.uuid4().hex[:6]}"
        args = self.build_run_args(container_name, command)
        process = await asyncio.create_subprocess_exec(
            self.docker,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        stdout_reader = asyncio.create_task(self._capture_tail(process.stdout))
        stderr_reader = asyncio.create_task(self._capture_tail(process.stderr))
        timed_out = False
        try:
            try:
                await _await_runtime_operation(
                    process.wait(),
                    cancellation_token,
                    timeout=timeout,
                    stage="docker_container",
                )
            except asyncio.TimeoutError:
                timed_out = True
                await _cleanup(self._remove_container(container_name))
                await _cleanup(terminate_process_tree(process))
            except ToolCancelledError as exc:
                await _cleanup(self._remove_container(container_name))
                await _cleanup(terminate_process_tree(process))
                exc.details.update(
                    {
                        "runtime": "docker",
                        "container_name": container_name,
                        "container_removed": True,
                    }
                )
                raise
            except asyncio.CancelledError:
                await _cleanup(self._remove_container(container_name))
                await _cleanup(terminate_process_tree(process))
                raise
            stdout, stdout_truncated = await stdout_reader
            stderr, stderr_truncated = await stderr_reader
            if timed_out:
                raise TimeoutError(f"Command timed out after {timeout:g} seconds")
            return CommandExecution(
                output=stdout + stderr,
                exit_code=process.returncode or 0,
                details={
                    "runtime": "docker",
                    "image": self.settings.docker_image,
                    "network": self.settings.docker_network,
                    "container_name": container_name,
                    "capture_truncated": stdout_truncated or stderr_truncated,
                    "streams_separated": True,
                },
                stdout=stdout,
                stderr=stderr,
            )
        finally:
            for reader in (stdout_reader, stderr_reader):
                if not reader.done():
                    reader.cancel()
                with contextlib.suppress(BaseException):
                    await reader
            await _cleanup(self._remove_container(container_name))

    def build_run_args(self, container_name: str, command: str) -> list[str]:
        workspace = str(self.workspace.root)
        if "," in workspace:
            raise ValueError("Docker workspace path cannot contain a comma")
        args = [
            "run",
            "--rm",
            "--name",
            container_name,
            "--pull",
            "never",
            "--network",
            self.settings.docker_network,
            "--cpus",
            str(self.settings.docker_cpus),
            "--memory",
            f"{self.settings.docker_memory_mb}m",
            "--memory-swap",
            f"{self.settings.docker_memory_mb}m",
            "--pids-limit",
            str(self.settings.docker_pids_limit),
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            f"/tmp:rw,nosuid,nodev,noexec,size={self.settings.docker_tmpfs_mb}m",
            "--ulimit",
            "nofile=1024:1024",
            "--log-driver",
            "none",
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,source={workspace},target=/workspace",
        ]
        for source, target in self._prepare_mask_mounts():
            if "," in str(source):
                raise ValueError("Docker mask path cannot contain a comma")
            args.extend(
                ["--mount", f"type=bind,source={source},target={target},readonly"]
            )
        for value in ("HOME=/tmp", "TMPDIR=/tmp", "NO_COLOR=1", "CI=1"):
            args.extend(["--env", value])
        args.extend([self.settings.docker_image, "sh", "-c", command])
        return args

    async def _capture_tail(
        self,
        stream: asyncio.StreamReader | None,
    ) -> tuple[bytes, bool]:
        if stream is None:
            return b"", False
        captured = bytearray()
        truncated = False
        while chunk := await stream.read(64 * 1024):
            captured.extend(chunk)
            if len(captured) > self.settings.max_capture_bytes:
                truncated = True
                del captured[: len(captured) - self.settings.max_capture_bytes]
        return bytes(captured), truncated

    async def _remove_container(self, container_name: str) -> None:
        cleanup: asyncio.subprocess.Process | None = None
        try:
            cleanup = await asyncio.create_subprocess_exec(
                self.docker,
                "rm",
                "-f",
                container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(cleanup.wait(), timeout=10)
        except asyncio.TimeoutError:
            if cleanup is not None and cleanup.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    cleanup.kill()
                with contextlib.suppress(BaseException):
                    await cleanup.wait()
        except OSError:
            return

    def _prepare_mask_mounts(self) -> list[tuple[Path, str]]:
        mask_root = Path(tempfile.gettempdir()) / "miniclaw-masks" / self.task_hash
        empty_directory = mask_root / "empty-directory"
        empty_file = mask_root / "empty-file"
        empty_directory.mkdir(parents=True, exist_ok=True)
        empty_file.parent.mkdir(parents=True, exist_ok=True)
        empty_file.write_text("", encoding="utf-8")
        mounts: list[tuple[Path, str]] = []
        for protected in self.workspace.protected_paths_for("execute"):
            if not protected.exists():
                continue
            source = empty_directory if protected.is_dir() else empty_file
            mounts.append((source, self.workspace.to_execution_path(protected)))
        return mounts


async def _await_runtime_operation(
    awaitable,
    cancellation_token: CancellationToken | None,
    *,
    timeout: float | None,
    stage: str,
):
    operation = asyncio.ensure_future(awaitable)
    if cancellation_token is not None:
        try:
            cancellation_token.raise_if_tool_cancelled(stage=stage)
        except BaseException:
            operation.cancel()
            with contextlib.suppress(BaseException):
                await operation
            raise
    cancellation = (
        asyncio.create_task(cancellation_token.wait())
        if cancellation_token is not None
        else None
    )
    waiters = {operation}
    if cancellation is not None:
        waiters.add(cancellation)
    try:
        done, _ = await asyncio.wait(
            waiters,
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if operation in done:
            return operation.result()
        operation.cancel()
        with contextlib.suppress(BaseException):
            await operation
        if cancellation is not None and cancellation in done:
            raise ToolCancelledError(cancellation_token.reason, stage=stage)
        raise asyncio.TimeoutError
    except asyncio.CancelledError:
        operation.cancel()
        with contextlib.suppress(BaseException):
            await operation
        raise
    finally:
        if cancellation is not None:
            cancellation.cancel()
            with contextlib.suppress(BaseException):
                await cancellation


async def _cleanup(awaitable) -> None:
    task = asyncio.ensure_future(awaitable)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        with contextlib.suppress(BaseException):
            await task
    except BaseException:
        return


def validate_docker_environment(settings: RuntimeSettings, docker: str | None = None) -> None:
    if settings.backend != "docker":
        return
    executable = docker or shutil.which("docker") or "docker"
    try:
        subprocess.run(
            [executable, "version", "--format", "{{.Server.Version}}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "Docker is unavailable. Start Docker Desktop or use --sandbox host."
        ) from exc
    try:
        subprocess.run(
            [executable, "image", "inspect", settings.docker_image],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"Docker image '{settings.docker_image}' is not present. Pull it explicitly first."
        ) from exc
