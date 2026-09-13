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
        nxt = next((e for e in state['events'].values() if e['tenant'] == event['tenant'] and e['order_id'] == event['order_id'] and e['seq'] == event['seq']), None)
        if nxt is None or [nxt['tenant'],nxt['event_id']] in state['effects']: break
        row['seq'] += 1; row['total'] += nxt['amount']; state['effects'].append([nxt['tenant'], nxt['event_id']])
    save(root, 'worker.json', state, fault, 'after_commit')
    return copy.deepcopy(row)

execute=worker
