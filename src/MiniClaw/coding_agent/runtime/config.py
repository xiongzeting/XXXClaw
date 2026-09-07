from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Mapping


RuntimeBackend = Literal["host", "docker"]
WorkspaceMode = Literal["direct", "snapshot"]
DockerNetwork = Literal["none", "bridge"]


@dataclass(slots=True, frozen=True)
class RuntimeSettings:
    """Configuration for the command runtime and its task workspace."""

    backend: RuntimeBackend = "docker"
    workspace_mode: WorkspaceMode = "direct"
    docker_image: str = "miniclaw-runtime:py311"
    docker_cpus: float = 1.0
    docker_memory_mb: int = 1024
    docker_pids_limit: int = 256
    docker_network: DockerNetwork = "none"
    docker_tmpfs_mb: int = 128
    snapshot_max_bytes: int = 512 * 1024 * 1024
    snapshot_max_files: int = 50_000
    default_command_timeout_seconds: float = 120.0
    max_command_timeout_seconds: float = 900.0
    max_capture_bytes: int = 10 * 1024 * 1024

    @property
    def sandbox(self) -> str:
        if self.backend == "host":
            return "host"
        return f"docker:{self.docker_image}"


def load_runtime_settings(
    environment: Mapping[str, str] | None = None,
    *,
    sandbox: str | None = None,
    workspace_mode: str | None = None,
    docker_image: str | None = None,
) -> RuntimeSettings:
    """Load MiniClaw Runtime settings using only MINICLAW_* configuration names."""

    env = os.environ if environment is None else environment
    sandbox_value = (
        sandbox or _first(env, "MINICLAW_SANDBOX") or "docker"
    ).strip()
    backend, image_from_sandbox = _parse_sandbox(sandbox_value)
    mode = (workspace_mode or _first(env, "MINICLAW_WORKSPACE_MODE") or "direct").strip().lower()
    if mode not in {"direct", "snapshot"}:
        raise ValueError("MINICLAW_WORKSPACE_MODE must be direct or snapshot")

    image = (
        docker_image
        or image_from_sandbox
        or _first(env, "MINICLAW_DOCKER_IMAGE")
        or "miniclaw-runtime:py311"
    ).strip()
    if not image:
        raise ValueError("Docker image cannot be empty")

    issues: list[str] = []
    cpus = _positive_float(
        _first(env, "MINICLAW_DOCKER_CPUS"),
        1.0,
        "MINICLAW_DOCKER_CPUS",
        issues,
    )
    memory_mb = _positive_int(
        _first(env, "MINICLAW_DOCKER_MEMORY_MB"),
        1024,
        "MINICLAW_DOCKER_MEMORY_MB",
        issues,
    )
    pids_limit = _positive_int(
        _first(env, "MINICLAW_DOCKER_PIDS_LIMIT"),
        256,
        "MINICLAW_DOCKER_PIDS_LIMIT",
        issues,
    )
    tmpfs_mb = _positive_int(
        _first(env, "MINICLAW_DOCKER_TMPFS_MB"),
        128,
        "MINICLAW_DOCKER_TMPFS_MB",
        issues,
    )
    snapshot_mb = _positive_int(
        _first(env, "MINICLAW_SNAPSHOT_MAX_MB"), 512, "MINICLAW_SNAPSHOT_MAX_MB", issues
    )
    snapshot_files = _positive_int(
        _first(env, "MINICLAW_SNAPSHOT_MAX_FILES"), 50_000, "MINICLAW_SNAPSHOT_MAX_FILES", issues
    )
    default_timeout = _positive_float(
        _first(env, "MINICLAW_COMMAND_TIMEOUT_SECONDS"),
        120.0,
        "MINICLAW_COMMAND_TIMEOUT_SECONDS",
        issues,
    )
    max_timeout = _positive_float(
        _first(env, "MINICLAW_COMMAND_MAX_TIMEOUT_SECONDS"),
        900.0,
        "MINICLAW_COMMAND_MAX_TIMEOUT_SECONDS",
        issues,
    )
    max_capture_mb = _positive_int(
        _first(env, "MINICLAW_COMMAND_MAX_CAPTURE_MB"),
        10,
        "MINICLAW_COMMAND_MAX_CAPTURE_MB",
        issues,
    )
    network = (
        _first(env, "MINICLAW_DOCKER_NETWORK") or "none"
    ).strip().lower()
    if network not in {"none", "bridge"}:
        issues.append("MINICLAW_DOCKER_NETWORK must be none or bridge")
    if memory_mb < 64:
        issues.append("MINICLAW_DOCKER_MEMORY_MB must be at least 64")
    if pids_limit < 16:
        issues.append("MINICLAW_DOCKER_PIDS_LIMIT must be at least 16")
    if tmpfs_mb < 16:
        issues.append("MINICLAW_DOCKER_TMPFS_MB must be at least 16")
    if default_timeout > max_timeout:
        issues.append("MINICLAW_COMMAND_TIMEOUT_SECONDS cannot exceed the maximum timeout")
    if issues:
        raise ValueError("; ".join(issues))

    return RuntimeSettings(
        backend=backend,
        workspace_mode=mode,  # type: ignore[arg-type]
        docker_image=image,
        docker_cpus=cpus,
        docker_memory_mb=memory_mb,
        docker_pids_limit=pids_limit,
        docker_network=network,  # type: ignore[arg-type]
        docker_tmpfs_mb=tmpfs_mb,
        snapshot_max_bytes=snapshot_mb * 1024 * 1024,
        snapshot_max_files=snapshot_files,
        default_command_timeout_seconds=default_timeout,
        max_command_timeout_seconds=max_timeout,
        max_capture_bytes=max_capture_mb * 1024 * 1024,
    )


def _parse_sandbox(value: str) -> tuple[RuntimeBackend, str | None]:
    normalized = value.strip()
    if normalized == "host":
        return "host", None
    if normalized == "docker":
        return "docker", None
    if normalized.startswith("docker:"):
        image = normalized.removeprefix("docker:").strip()
        if not image:
            raise ValueError("Use host, docker, or docker:<image> for the sandbox")
        return "docker", image
    raise ValueError("Use host, docker, or docker:<image> for the sandbox")


def _first(environment: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = environment.get(name)
        if value is not None and value.strip():
            return value
    return None


def _positive_int(raw: str | None, fallback: int, name: str, issues: list[str]) -> int:
    if raw is None:
        return fallback
    try:
        value = int(raw)
    except ValueError:
        issues.append(f"{name} must be a positive integer")
        return fallback
    if value <= 0:
        issues.append(f"{name} must be a positive integer")
        return fallback
    return value


def _positive_float(raw: str | None, fallback: float, name: str, issues: list[str]) -> float:
    if raw is None:
        return fallback
    try:
        value = float(raw)
    except ValueError:
        issues.append(f"{name} must be a positive number")
        return fallback
    if value <= 0:
        issues.append(f"{name} must be a positive number")
        return fallback
    return value
