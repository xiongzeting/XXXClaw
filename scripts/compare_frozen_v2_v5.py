"""Compare frozen source versions without importing or executing MiniClaw."""
from pathlib import Path
import hashlib
import json
import difflib

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / '.aster/evals'
OUT = BASE / 'efficiency-revision-v5/design-diff'
OUT.mkdir(exist_ok=True)
versions = ['v2', 'v3', 'v4', 'v5']
maps = {}
for version in versions:
    source = BASE / f'efficiency-revision-{version}/snapshot/src'
    maps[version] = {p.relative_to(source).as_posix(): p for p in source.rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
rows = []
for rel in sorted(set(maps['v2']) | set(maps['v5'])):
    contents = {v: maps[v][rel].read_bytes() if rel in maps[v] else None for v in versions}
    if contents['v2'] == contents['v5']:
        continue
    transitions = [f'{a}->{b}' for a, b in zip(versions, versions[1:]) if contents[a] != contents[b]]
    row = {'file': rel, 'status': 'added' if contents['v2'] is None else 'deleted' if contents['v5'] is None else 'modified',
           'transitions': transitions, 'sha256': {v: hashlib.sha256(x).hexdigest() if x is not None else None for v, x in contents.items()}}
    rows.append(row)
    old = (contents['v2'] or b'').decode('utf-8').splitlines(keepends=True)
    new = (contents['v5'] or b'').decode('utf-8').splitlines(keepends=True)
    (OUT / (rel.replace('/', '__') + '.diff')).write_text(''.join(difflib.unified_diff(old, new, fromfile='v2/'+rel, tofile='v5/'+rel)), encoding='utf-8')
unchanged = sorted(rel for rel in set(maps['v2']) & set(maps['v5']) if maps['v2'][rel].read_bytes() == maps['v5'][rel].read_bytes())
(OUT / 'index.json').write_text(json.dumps({'changed': rows, 'unchanged': unchanged}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
for r in rows:
    print(r['status'], r['file'], ', '.join(r['transitions']))
print('TOTAL_CHANGED', len(rows), 'UNCHANGED', len(unchanged))
