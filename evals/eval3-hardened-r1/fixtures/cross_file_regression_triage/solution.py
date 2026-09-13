import copy,json
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
def integer(x, minimum=0):
    if type(x) is not int or x < minimum:
        raise ValueError('integer')
    return x

def report(data):
    grouped = {}
    for e in data['events']:
        k = e['id']
        if k not in grouped or e['revision'] > grouped[k]['revision']: grouped[k] = e
        elif e['revision'] == grouped[k]['revision'] and e != grouped[k]: raise ValueError('conflict')
    output = []
    for q in data['queries']:
        days = defaultdict(list)
        for e in grouped.values():
            if e['tenant'] != q['tenant']: continue
            t = datetime.fromisoformat(e['time'].replace('Z', '+00:00'))
            if t.tzinfo is None: raise ValueError('offset')
            day = t.date().isoformat()
            if q['start'] <= day <= q['end']: days[day].append(e)
        output.append({'query_id': q['query_id'], 'days': [{'date': day, 'ids': sorted(e['id'] for e in es), 'total': sum(e['amount'] for e in es)} for day, es in sorted(days.items())]})
    return output

solve=report
