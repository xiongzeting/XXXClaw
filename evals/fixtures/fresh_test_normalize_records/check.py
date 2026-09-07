import json
from pathlib import Path
assert json.loads(Path('active_ids.json').read_text()) == ['a2','m4']
print('CHECK_OK')
