from pathlib import Path
import json
def save(path,data): Path(path).write_text(json.dumps(data))
