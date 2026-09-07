from pathlib import Path
import json
Path('out/summary.json').write_text(json.dumps({'items': 23, 'ready': True}))
