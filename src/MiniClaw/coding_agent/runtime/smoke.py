from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import RuntimeSettings
from .core import create_tool_runtime


async def run(image: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="miniclaw-runtime-smoke-") as directory:
        root = Path(directory)
        source = root / "source"
        source.mkdir()
        (source / "input.txt").write_text("runtime-smoke\n", encoding="utf-8")
        (source / ".env").write_text("SMOKE_SECRET=must-not-enter\n", encoding="utf-8")
        runtime = create_tool_runtime(
            source,
            root / "session",
            "runtime-smoke",
            RuntimeSettings(
                backend="docker",
                docker_image=image,
                docker_network="none",
                default_command_timeout_seconds=30,
            ),
        )
        command = (
            "test ! -s .env && "
            "python -c \"from pathlib import Path; Path('docker-smoke.txt').write_text(Path('input.txt').read_text())\""
        )
        execution = await runtime.run(
            command,
            runtime.host_workspace,
            30,
        )
        if execution.exit_code != 0:
            raise RuntimeError(
                execution.output.decode("utf-8", errors="replace")
                or f"Container exited with {execution.exit_code}"
            )
        if (source / "docker-smoke.txt").read_text(encoding="utf-8") != "runtime-smoke\n":
            raise RuntimeError("Docker workspace result did not persist to source")
        timed_out = False
        try:
            await runtime.run("sleep 5", runtime.host_workspace, 0.2)
        except TimeoutError:
            timed_out = True
        if not timed_out:
            raise RuntimeError("Docker timeout did not interrupt the command")
        docker = shutil.which("docker") or "docker"
        task_hash = hashlib.sha256("runtime-smoke".encode("utf-8")).hexdigest()[:12]
        containers = subprocess.run(
            [docker, "ps", "-a", "--filter", f"name=miniclaw-{task_hash}-", "--format", "{{.Names}}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        if containers:
            raise RuntimeError(f"Timed-out Runtime container was not removed: {containers}")
        return {
            "status": "ok",
            "image": image,
            "runtime": execution.details.get("runtime"),
            "network": execution.details.get("network"),
            "direct_persisted": True,
            "direct_secret_masked": True,
            "timeout_cleanup": True,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an isolated MiniClaw Docker Runtime smoke test")
    parser.add_argument("--image", default="miniclaw-runtime:py311")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    print(json.dumps(asyncio.run(run(args.image)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
