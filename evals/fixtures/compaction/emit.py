from __future__ import annotations

import sys


mode = sys.argv[1] if len(sys.argv) > 1 else "medium-a"
markers = {
    "large": "L1-LARGE-RESULT-731",
    "medium-a": "ARCHIVE-RECOVERY-731",
    "medium-b": "SECONDARY-EVIDENCE-884",
}
marker = markers.get(mode, "UNKNOWN")
size = 20_000 if mode == "large" else 5_000
print(marker)
print((mode.upper() + "-") * max(1, size // (len(mode) + 1)))
