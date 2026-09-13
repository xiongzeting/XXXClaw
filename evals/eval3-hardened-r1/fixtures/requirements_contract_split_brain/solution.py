import copy,json
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
def integer(x, minimum=0):
    if type(x) is not int or x < minimum:
        raise ValueError('integer')
    return x

def protocol(data):
    def respond(item, default):
        version = default
        if version not in (1, 2): return {'status': 400, 'body': {'error': 'E_VERSION'}}
        kind = item.get('kind', 'ok')
        statuses = {'missing': 404, 'invalid': 400, 'conflict': 409}
        codes = {'missing': 1, 'invalid': 2, 'conflict': 3}
        if kind == 'ok': return {'status': 200, 'body': {'ok': True, 'error': None}}
        if kind not in codes: kind = 'invalid'
        return {'status': statuses[kind], 'body': {'ok': False, 'error': codes[kind] if version == 1 else 'E_' + kind.upper()}}
    if 'items' in data: return [respond(x, 1) for x in data['items']]
    return respond(data, 1)

solve=protocol
