import sys,json
from pathlib import Path
p=Path('pipeline.json')
state=json.loads(p.read_text()) if p.exists() else []
step=sys.argv[1]
expected=['extract','validate','publish']
assert len(state)<3 and step==expected[len(state)], 'wrong order or duplicated step'
state.append(step)
p.write_text(json.dumps(state))
print('completed '+step)
