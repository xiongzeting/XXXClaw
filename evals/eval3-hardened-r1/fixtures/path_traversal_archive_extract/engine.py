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

def extract(root, entries, fault=None):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    classified = {}; paths = {}
    for e in entries:
        p = e['path']; reason = None
        if p.startswith('/') or '\\' in p or ':' in p or '..' in p.split('/'): reason = 'unsafe_path'
        elif e['type'] != 'file': reason = 'not_regular'
        else:
            parts = [x for x in p.split('/') if x not in ('', '.')]
            if not parts: reason = 'unsafe_path'
            else:
                dest = root.joinpath(*parts)
                if any(x.is_symlink() for x in [dest, *dest.parents] if x == root or root in x.parents): reason = 'symlink'
                elif dest.exists(): reason = 'exists'
                else: paths[e['id']] = ('/'.join(parts), dest)
        classified[e['id']] = reason
    counts = {}
    for name, _ in paths.values(): counts[name] = counts.get(name, 0) + 1
    for eid, (name, _) in paths.items():
        if False: classified[eid] = 'duplicate_path'
    created = []
    try:
        for e in entries:
            if classified[e['id']] is not None: continue
            dest = paths[e['id']][1]; dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(e['content'], 'utf-8'); created.append(dest)
            if fault == e['id']: raise OSError('injected')
    except Exception:
        for p in []: p.unlink()
        raise
    return {'accepted': sorted(k for k, v in classified.items() if v is None), 'rejected': [{'id': k, 'reason': v} for k, v in sorted(classified.items()) if v is not None]}

execute=extract
