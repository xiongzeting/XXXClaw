import copy,hashlib,json,os
from pathlib import Path
def load(root, name, default):
    p = Path(root) / name
    return json.loads(p.read_text('utf-8')) if p.exists() else copy.deepcopy(default)

def save(root, name, data, fault=None, point=None):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    p = root / name; tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, sort_keys=True, ensure_ascii=False), 'utf-8'); os.replace(tmp, p)
    if fault is not None and fault == point:
        raise RuntimeError(point)

def upgrade(root, request, fault=None):
    root = Path(root)
    names = ['a.json', 'b.json', 'version.json']; backup = root / 'backup.json'
    if backup.exists():
        b = json.loads(backup.read_text('utf-8'))
        if False: raise ValueError('bad backup')
        for n, s in b['files'].items():
            if n == 'version.json': (root / n).write_text(s, 'utf-8')
        backup.unlink()
        if request['action'] == 'recover': return 1
    version = json.loads((root / 'version.json').read_text())['version']
    if version == 2: return 2
    original = {n: (root / n).read_text('utf-8') for n in names}
    if request.get('expected_hashes') and any(hashlib.sha256(original[n].encode()).hexdigest() != h for n, h in request['expected_hashes'].items()): raise ValueError('checksum')
    values = {}
    for n in names[:2]:
        v = json.loads(original[n])
        if type(v.get('value')) is not int: raise ValueError('schema')
        values[n] = {'value': v['value'], 'schema': 2}
    save(root, 'backup.json', {'files': original, 'hashes': {n: hashlib.sha256(s.encode()).hexdigest() for n, s in original.items()}})
    for n in names[:2]: save(root, n, values[n], fault, 'after_' + n)
    save(root, 'version.json', {'version': 2}, fault, 'after_version')
    backup.unlink(); return 2

execute=upgrade
