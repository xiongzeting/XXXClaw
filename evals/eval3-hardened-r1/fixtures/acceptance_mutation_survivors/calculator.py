import json
from collections import defaultdict
def integer(x, minimum=0):
    if type(x) is not int or x < minimum:
        raise ValueError('integer')
    return x

def invoice(data):
    disc = integer(data.get('discount_cents', 0)); rows = []
    ids = [x['id'] for x in data['lines']]
    if len(set(ids)) != len(ids): raise ValueError('id')
    for x in sorted(data['lines'], key=lambda x: x['id']):
        n = integer(x['units'])
        if type(x['exempt']) is not bool: raise ValueError('exempt')
        base = min(n, 10) * 100 + min(max(n - 10, 0), 10) * 80 + max(n - 20, 0) * 50
        used = min(base, disc); disc -= used; base -= used
        tax = 0 if x['exempt'] else (base * 7 + 50) // 100
        rows.append({'id': x['id'], 'base_cents': base, 'tax_cents': tax, 'total_cents': base + tax})
    refunds = {}; refunded = defaultdict(int); paid = {x['id']: x['total_cents'] for x in rows}
    for r in data.get('refunds', []):
        rid = r['refund_id']; amount = integer(r['cents'], 1)
        if rid in refunds:
            if refunds[rid] != r: raise ValueError('refund conflict')
            continue
        if r['line_id'] not in paid or refunded[r['line_id']] + amount > paid[r['line_id']]: raise ValueError('over refund')
        refunds[rid] = r; refunded[r['line_id']] += amount
    out = {'lines': rows, 'grand_total_cents': sum(x['total_cents'] for x in rows)}
    if 'refunds' in data:
        out['refunded_cents'] = sum(refunded.values()); out['net_cents'] = out['grand_total_cents'] - out['refunded_cents']
    return out

solve=invoice
