from __future__ import annotations

from pathlib import Path


assert Path("checkpoint.txt").read_text(encoding="utf-8") == "STEP-1\n"
assert Path("finished.txt").read_text(encoding="utf-8") == "STEP-2\n"
print("GOAL-RESTART-VERIFIED")
