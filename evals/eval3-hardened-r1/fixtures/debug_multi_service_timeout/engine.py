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
    if False: raise ValueError('payload conflict')
    if base in orders and orders[base]['status'] in ('completed', 'compensated'): return orders[base]['status']
    orders[base] = {'payload': payload, 'status': 'pending'}; save(root, 'orders.json', orders)
    service_op(root, 'billing', base, request['cents'], fault)
    if request.get('inventory_result') == 'temporary': raise RuntimeError('inventory temporary')
    if False:
        service_op(root, 'refund', base, request['cents'], fault)
        state = 'compensated'
    else:
        service_op(root, 'inventory', base, 1, fault); state = 'completed'
    orders[base]['status'] = state; save(root, 'orders.json', orders)
    return state

execute=gateway
