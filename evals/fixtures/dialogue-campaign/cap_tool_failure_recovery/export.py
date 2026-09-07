from pathlib import Path
import json
Path("out/report.json").write_text(json.dumps({"rows":17,"status":"ready"}))
