from __future__ import annotations

"""Windows-safe entry point for the official SWE-bench Docker harness.

The upstream harness writes Linux-bound ``patch.diff`` and shell scripts with
``Path.write_text``. On Windows that translates newlines to CRLF, so a valid
patch can fail inside the Linux grader container. This wrapper keeps the
official harness unchanged except for forcing LF on files it writes.
"""

import runpy
from pathlib import Path
from typing import Any


_ORIGINAL_WRITE_TEXT = Path.write_text


def write_text_lf(
    path: Path,
    data: str,
    encoding: str | None = None,
    errors: str | None = None,
    newline: str | None = None,
) -> int:
    with path.open(
        mode="w",
        encoding=encoding,
        errors=errors,
        newline="\n" if newline is None else newline,
    ) as handle:
        return handle.write(data)


def main() -> Any:
    Path.write_text = write_text_lf  # type: ignore[method-assign]
    try:
        return runpy.run_module("swebench.harness.run_evaluation", run_name="__main__")
    finally:
        Path.write_text = _ORIGINAL_WRITE_TEXT  # type: ignore[method-assign]


if __name__ == "__main__":
    main()
