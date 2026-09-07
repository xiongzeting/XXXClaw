from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def read_env_file(path: str | Path) -> dict[str, str]:
    """Read a small dotenv-compatible file without changing process-global state."""

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Environment file not found: {source}")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        source.read_text(encoding="utf-8-sig", errors="strict").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"Invalid environment line {line_number} in {source}")
        name, raw_value = line.split("=", 1)
        name = name.strip()
        if not name or not (name[0].isalpha() or name[0] == "_") or not all(
            character.isalnum() or character == "_" for character in name
        ):
            raise ValueError(f"Invalid environment variable name on line {line_number}")
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[name] = value
    return values


def merged_environment(
    file_values: Mapping[str, str],
    process_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Use a dotenv file as fallback while allowing explicit process variables to win."""

    result = dict(file_values)
    result.update(os.environ if process_environment is None else process_environment)
    return result
