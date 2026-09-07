"""Versioned, requirement-linked oracles executed outside the submitted workspace.

An oracle problem is an invalid measurement, never evidence of model failure.
The original command check remains available for legacy frozen suites.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping


def decode_result(output: str, returncode: int, requirements: Mapping[str, str]) -> dict[str, Any]:
    try:
        value = json.loads(output)
        if not isinstance(value, dict) or value.get("protocol") != "miniclaw-oracle-v1":
            raise ValueError("missing oracle protocol")
        if value.get("status") == "oracle_invalid":
            raise ValueError(str(value.get("error") or "oracle exception"))
        checks = value["checks"]
        if not isinstance(checks, list) or not checks:
            raise ValueError("missing requirement checks")
        seen = set()
        for check in checks:
            key = check["requirement_id"]
            if key not in requirements or key in seen:
                raise ValueError("unknown or duplicate requirement_id")
            seen.add(key)
            if type(check["passed"]) is not bool or not isinstance(check.get("evidence"), str):
                raise ValueError("invalid check evidence or boolean")
        if seen != set(requirements):
            raise ValueError("oracle did not check every declared requirement")
        passed = all(c["passed"] for c in checks)
        expected = "passed" if passed else "capability_failure"
        if value.get("status") != expected or returncode != (0 if passed else 10):
            raise ValueError("oracle exit/status/checks disagree")
        return {"passed": passed, "classification": expected, "checks": checks}
    except (ValueError, TypeError, KeyError) as exc:
        return {"passed": False, "classification": "oracle_invalid", "error": str(exc)}


def evaluate_oracle(options: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    """Run trusted checker in Linux, with submission and checker mounted read-only."""
    try:
        script = Path(options["script"]).resolve()
        expected = options["sha256"]
        requirements = options["requirements"]
        if not isinstance(requirements, dict) or not requirements or not all(
            isinstance(k, str) and k and isinstance(v, str) and v.strip() for k, v in requirements.items()
        ):
            raise ValueError("oracle requires public requirement IDs and descriptions")
        if hashlib.sha256(script.read_bytes()).hexdigest() != expected:
            raise ValueError("oracle SHA-256 differs from frozen contract")
        command = ["docker", "run", "--rm", "--network", "none", "--read-only",
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "64",
                   "--memory", "256m", "--cpus", "1", "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
                   "--mount", f"type=bind,source={workspace.resolve()},target=/workspace,readonly",
                   "--mount", f"type=bind,source={script},target=/oracle/check.py,readonly",
                   "-w", "/workspace", "-e", "PYTHONDONTWRITEBYTECODE=1",
                   str(options.get("image", "miniclaw-runtime:py313-bench")),
                   "python", "/oracle/check.py", str(options["entry"])]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"passed": False, "classification": "oracle_invalid", "error": str(exc)}
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=float(options.get("timeout_seconds", 90)))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"passed": False, "classification": "runtime_failure", "error": str(exc)}
    if result.returncode in (125, 126, 127):
        return {"passed": False, "classification": "runtime_failure", "error": result.stderr[-2000:]}
    decoded = decode_result(result.stdout, result.returncode, requirements)
    return {**decoded, "exit_code": result.returncode, "stderr": result.stderr[-2000:], "sha256": expected}
