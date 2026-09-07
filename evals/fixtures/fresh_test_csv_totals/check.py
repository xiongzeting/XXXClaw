import json
from pathlib import Path
assert json.loads(Path('totals.json').read_text()) == {'east':50,'west':200}
print('CHECK_OK')
