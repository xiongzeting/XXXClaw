"""Best-effort discovery of execution and browser capabilities.

The result is descriptive evidence for prompts and reports; a missing optional
integration is reported as unavailable instead of being inferred from PATH.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path
from typing import Any


def discover_execution_capabilities(*, runtime: str = "host", docker: str | None = None) -> dict[str, Any]:
    """Discover capabilities available to the selected host or Docker runtime."""
    runtime = runtime.lower()
    edge_path = _find_edge() if runtime != "docker" else None
    playwright_module = importlib.util.find_spec("playwright") if runtime != "docker" else None
    playwright_cli = shutil.which("playwright") if runtime != "docker" else None
    node_playwright = _find_node_playwright() if runtime != "docker" else None
    docker_path = shutil.which(docker or "docker") if runtime == "docker" else None
    discovered = ["bash"]
    if edge_path and (playwright_module or node_playwright):
        discovered.append("edge_playwright_runtime")
    return {
        "runtime": runtime,
        "registered": [],
        "discovered": discovered,
        "environment": {
            "edge": {"available": edge_path is not None, "path": str(edge_path) if edge_path else None},
            "playwright": {
                "available": bool(playwright_module or node_playwright),
                "checked_in_runtime": runtime != "docker",
                "module_path": getattr(playwright_module, "origin", None),
                "cli_path": playwright_cli,
                "node_package_path": str(node_playwright) if node_playwright else None,
            },
            "docker": {
                "available": docker_path is not None,
                "path": docker_path,
                "checked_for": runtime == "docker",
            },
        },
    }


def _find_edge() -> Path | None:
    for name in ("msedge", "msedge.exe", "microsoft-edge", "microsoft-edge.exe"):
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    if os.name == "nt":
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
            base = os.environ.get(variable)
            if not base:
                continue
            candidate = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if candidate.is_file():
                return candidate.resolve()
    return None


def _find_node_playwright() -> Path | None:
    roots: list[Path] = []
    for key in ("MINICLAW_PLAYWRIGHT_ROOTS", "MINICLAW_RUNTIME_ROOT", "CODEX_RUNTIME_ROOT", "CODEX_HOME"):
        value = os.environ.get(key)
        if value:
            roots.extend(Path(item) for item in value.split(os.pathsep) if item)
    node = shutil.which("node")
    if node:
        roots.append(Path(node).parent)
    roots.append(Path.home() / ".cache")
    for root in roots:
        if not root.exists():
            continue
        for candidate in (root / "package.json", root / "node_modules/playwright/package.json",
                          root / "dependencies/node/node_modules/playwright/package.json"):
            if candidate.parent.name == "playwright" and candidate.is_file():
                return candidate.parent.resolve()
        try:
            # Probe documented layouts; never recursively scan the user's app data.
            for package in root.glob("codex-runtimes/*/dependencies/node/node_modules/playwright/package.json"):
                if package.is_file():
                    return package.parent.resolve()
        except OSError:
            continue
    return None


__all__ = ["discover_execution_capabilities"]
