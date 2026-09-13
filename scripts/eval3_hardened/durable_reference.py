"""Reference implementations exercised against real temporary filesystem state."""
import copy
import hashlib
import json
import os
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


def worker(root, event, fault=None):
    state = load(root, 'worker.json', {'events': {}, 'orders': {}, 'effects': []})
    key = json.dumps([event['tenant'], event['event_id']]); order = json.dumps([event['tenant'], event['order_id']])
    if key in state['events'] and state['events'][key] != event: raise ValueError('conflict')
    if type(event['seq']) is not int or event['seq'] < 1 or type(event['amount']) is not int: raise ValueError('event')
    # Different event ids with the same order/seq are a conflict too.
    for e in state['events'].values():
        if [e['tenant'], e['order_id'], e['seq']] == [event['tenant'], event['order_id'], event['seq']] and e != event: raise ValueError('sequence conflict')
    state['events'][key] = copy.deepcopy(event)
    row = state['orders'].setdefault(order, {'seq': 0, 'total': 0})
    while True:
        nxt = next((e for e in state['events'].values() if e['tenant'] == event['tenant'] and e['order_id'] == event['order_id'] and e['seq'] == row['seq'] + 1), None)
        if nxt is None: break
        row['seq'] += 1; row['total'] += nxt['amount']; state['effects'].append([nxt['tenant'], nxt['event_id']])
    save(root, 'worker.json', state, fault, 'after_commit')
    return copy.deepcopy(row)


def service_op(root, service, key, amount, fault=None):
    state = load(root, service + '.json', {})
    if key in state:
        if state[key] != amount: raise ValueError('payload conflict')
    else:
        state[key] = amount
        save(root, service + '.json', state, fault, service + '_after_commit')
    return state[key]


def gateway(root, request, fault=None):
    if type(request['cents']) is not int or request['cents'] <= 0: raise ValueError('cents')
    base = json.dumps([request['tenant'], request['order_id']])
    orders = load(root, 'orders.json', {})
    payload = {k: request[k] for k in ('tenant', 'order_id', 'cents', 'sku')}
    if base in orders and orders[base]['payload'] != payload: raise ValueError('payload conflict')
    if base in orders and orders[base]['status'] in ('completed', 'compensated'): return orders[base]['status']
    orders[base] = {'payload': payload, 'status': 'pending'}; save(root, 'orders.json', orders)
    service_op(root, 'billing', base, request['cents'], fault)
    if request.get('inventory_result') == 'temporary': raise RuntimeError('inventory temporary')
    if request.get('inventory_result') == 'permanent':
        service_op(root, 'refund', base, request['cents'], fault)
        state = 'compensated'
    else:
        service_op(root, 'inventory', base, 1, fault); state = 'completed'
    orders[base]['status'] = state; save(root, 'orders.json', orders)
    return state


def batch(root, request, fault=None):
    # action=restore closes the failed attempt; action=commit needs a fresh attempt id.
    key = request['batch_id']; aid = str(request['attempt_id']); payload = request['amounts']
    if set(payload) != {'t1', 't2', 't3'} or any(type(v) is not int or v <= 0 for v in payload.values()): raise ValueError('amounts')
    state = load(root, 'batches.json', {})
    row = state.setdefault(key, {'payload': payload, 'attempts': {}})
    if row['payload'] != payload: raise ValueError('payload conflict')
    if aid not in row['attempts']:
        if any(v == 'pending' for v in row['attempts'].values()): raise ValueError('unfinished attempt')
        row['attempts'][aid] = 'pending'; save(root, 'batches.json', state)
    status = row['attempts'][aid]
    if request['action'] == 'restore':
        if status == 'committed': raise ValueError('committed')
        for tenant in sorted(payload):
            ledger = load(root, tenant + '.json', {})
            op = f'{key}/{aid}/commit'
            if op in ledger:
                ledger[f'{key}/{aid}/compensate'] = -ledger[op]
                save(root, tenant + '.json', ledger, fault, 'compensate_' + tenant)
        row['attempts'][aid] = 'compensated'; save(root, 'batches.json', state)
        return 'all_pending'
    if status == 'compensated': return 'all_pending'
    for tenant, amount in sorted(payload.items()):
        if fault == 'before_' + tenant: raise RuntimeError(fault)
        ledger = load(root, tenant + '.json', {})
        op = f'{key}/{aid}/commit'
        ledger[op] = amount; save(root, tenant + '.json', ledger, fault, 'after_' + tenant)
    row['attempts'][aid] = 'committed'; save(root, 'batches.json', state)
    return 'committed'


def upgrade(root, request, fault=None):
    root = Path(root)
    names = ['a.json', 'b.json', 'version.json']; backup = root / 'backup.json'
    if backup.exists():
        b = json.loads(backup.read_text('utf-8'))
        if any(hashlib.sha256(s.encode()).hexdigest() != b['hashes'][n] for n, s in b['files'].items()): raise ValueError('bad backup')
        for n, s in b['files'].items(): (root / n).write_text(s, 'utf-8')
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


def release(root):
    root = Path(root); result = {}
    def walk(p):
        for child in sorted(p.iterdir()):
            if child.is_symlink() or child.name.startswith('.') or child.name == '__pycache__': continue
            if child.is_dir(): walk(child)
            elif child.suffix == '.py': result[child.relative_to(root).as_posix()] = hashlib.sha256(child.read_bytes()).hexdigest()
    walk(root)
    return dict(sorted(result.items()))


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
        if counts[name] > 1: classified[eid] = 'duplicate_path'
    created = []
    try:
        for e in entries:
            if classified[e['id']] is not None: continue
            dest = paths[e['id']][1]; dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(e['content'], 'utf-8'); created.append(dest)
            if fault == e['id']: raise OSError('injected')
    except Exception:
        for p in created: p.unlink()
        raise
    return {'accepted': sorted(k for k, v in classified.items() if v is None), 'rejected': [{'id': k, 'reason': v} for k, v in sorted(classified.items()) if v is not None]}
