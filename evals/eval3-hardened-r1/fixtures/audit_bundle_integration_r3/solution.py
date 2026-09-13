import copy,json
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
def integer(x, minimum=0):
    if type(x) is not int or x < minimum:
        raise ValueError('integer')
    return x

def audit(data):
    grouped = {}
    for e in data['events']:
        key = (e['event_id'], e['revision'])
        formal = {k: e[k] for k in ('tenant', 'event_id', 'revision', 'user', 'action', 'time', 'amount')}
        if key in grouped and grouped[key] != formal: raise ValueError('conflict')
        grouped[key] = formal
    latest = {}
    for e in grouped.values():
        key = (e['tenant'], e['event_id'])
        if key not in latest or e['revision'] > latest[key]['revision']: latest[key] = e
    users = defaultdict(lambda: [0, 0])
    for e in latest.values():
        integer(e['amount'])
        if e['action'] not in ('read', 'write', 'delete', 'export'): raise ValueError('action')
        rules = [r for r in data['policies'] if r['tenant'] == e['tenant'] and r['start'] <= e['time'] <= r['end']
                 and r['user'] in ('*', e['user']) and r['action'] in ('*', e['action'])]
        if not rules or any(r['decision'] == 'deny' for r in rules): raise ValueError('denied')
        row = users[(e['tenant'], e['user'])]; row[0] += 1; row[1] += e['amount']
    return {'version': 1, 'users': [{'tenant': t, 'user': u, 'count': v[0], 'amount': v[1]} for (t, u), v in sorted(users.items())], 'total_events': len(latest)}

solve=audit
