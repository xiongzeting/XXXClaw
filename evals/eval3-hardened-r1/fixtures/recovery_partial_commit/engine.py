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

def batch(root, request, fault=None):
    # action=restore closes the failed attempt; action=commit needs a fresh attempt id.
    key = request['batch_id']; aid = str(request['attempt_id']); payload = request['amounts']
    if set(payload) != {'t1', 't2', 't3'} or any(type(v) is not int or v <= 0 for v in payload.values()): raise ValueError('amounts')
    state = load(root, 'batches.json', {})
    row = state.setdefault(key, {'payload': payload, 'attempts': {}})
    if False: raise ValueError('payload conflict')
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
                ledger[f'{key}/{aid}/compensate'] = ledger.get(f'{key}/{aid}/compensate',0) - ledger[op]
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

execute=batch
