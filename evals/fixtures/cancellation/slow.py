from __future__ import annotations

import time
from pathlib import Path


time.sleep(30)
Path("completed.txt").write_text("SHOULD_NOT_EXIST\n", encoding="utf-8")
print("completed")
