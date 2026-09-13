"""Trusted reference semantics. Never copied into a candidate workspace."""
import copy
import itertools
import json
import re
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo


def integer(x, minimum=0):
    if type(x) is not int or x < minimum:
        raise ValueError('integer')
    return x


def migration(data):
    data = copy.deepcopy(data)
    if data.get('schema') not in (1, 2):
        raise ValueError('schema')
    profiles = data.get('profiles', {})
    resolved = {}
    def profile(name, seen=()):
        if name in seen or name not in profiles:
            raise ValueError('profile')
        if name not in resolved:
            row = profiles[name]
            resolved[name] = {**(profile(row['extends'], (*seen, name)) if 'extends' in row else {}),
                              **{k: v for k, v in row.items() if k != 'extends'}}
        return copy.deepcopy(resolved[name])
    for name in profiles:
        profile(name)
    def walk(node):
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for key, value in node.items():
            if key == 'profiles':
                out[key] = copy.deepcopy(value)
            elif key == 'services':
                if not isinstance(value, list):
                    raise ValueError('services')
                out[key] = []
                for item in value:
                    v = {**(profile(item['profile']) if 'profile' in item else {}),
                         **{k: x for k, x in item.items() if k not in ('profile', 'override')},
                         **item.get('override', {})}
                    has_host = 'host' in v or 'port' in v
                    if has_host:
                        if 'endpoint' in v or not isinstance(v.get('host'), str) or not v['host']:
                            raise ValueError('endpoint conflict')
                        port = integer(v.get('port'), 1)
                        if port > 65535:
                            raise ValueError('port')
                        v['endpoint'] = f"{v.pop('host')}:{v.pop('port')}"
                    elif not isinstance(v.get('endpoint'), str) or not v['endpoint']:
                        raise ValueError('endpoint')
                    out[key].append(walk(v))
            else:
                out[key] = walk(value)
        return out
    result = walk(data)
    result['schema'] = 2
    return result


def shipping(data):
    data = copy.deepcopy(data)
    packages = data['packages']
    byid = {p['id']: p for p in packages}
    if len(byid) != len(packages):
        raise ValueError('duplicate')
    changes = {}
    for a in data.get('amendments', []):
        key = (a['amendment_id'], integer(a['revision']))
        if key in changes and changes[key] != a:
            raise ValueError('conflicting revision')
        changes[key] = a
    latest = {}
    for (aid, revision), a in changes.items():
        if aid not in latest or revision > latest[aid]['revision']:
            latest[aid] = a
    free = set(data.get('free_ids', []))
    for a in sorted(latest.values(), key=lambda x: (x['revision'], x['amendment_id'])):
        if a.get('withdrawn', False):
            continue
        if a['package_id'] not in byid:
            raise ValueError('unknown package')
        for k, v in a['set'].items():
            if k == 'free':
                (free.add if v else free.discard)(a['package_id'])
            else:
                byid[a['package_id']][k] = v
    caps = {k: integer(v) for k, v in data.get('group_caps', {}).items()}
    cap = data.get('cap_cents')
    if cap is not None:
        integer(cap)
    result = []
    for p in sorted(packages, key=lambda p: p['id']):
        w = integer(p['weight_g'])
        if p['zone'] not in ('local', 'remote') or type(p['fragile']) is not bool:
            raise ValueError('package')
        cost = 500 + max(0, (w - 1) // 1000) * 200 + 300 * (p['zone'] == 'remote') + 150 * p['fragile']
        if p['id'] in free:
            cost = 0
        group = p.get('group_id', '')
        if group in caps:
            cost = min(cost, caps[group]); caps[group] -= cost
        result.append({'id': p['id'], 'shipping_cents': cost})
    if cap is not None:
        for row in result:
            row['shipping_cents'] = min(row['shipping_cents'], cap)
            cap -= row['shipping_cents']
    return result


def resolution(data):
    def ver(x):
        if not isinstance(x, str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', x):
            raise ValueError('version')
        return tuple(map(int, x.split('.')))
    catalog = data['catalog']; deps = data.get('dependencies', {}); yanked = data.get('yanked', {})
    def valid(r):
        lo = ver(r['min']); hi = ver(r['max']) if r['max'] is not None else None
        if hi is not None and lo >= hi:
            raise ValueError('range')
    def match(v, r):
        return ver(v) >= ver(r['min']) and (r['max'] is None or ver(v) < ver(r['max']))
    for values in (*catalog.values(), *yanked.values()):
        for v in values: ver(v)
    reqs = list(data['requirements'])
    for r in reqs: valid(r)
    for name, versions in deps.items():
        for v, rows in versions.items():
            ver(v)
            for r in rows: valid(r)
    names = sorted(catalog)
    # Enumerative independent oracle: suite keeps candidate product <= 100000.
    options = [[None] + sorted(set(catalog[n]) - set(yanked.get(n, [])), key=ver) for n in names]
    best = None; best_key = None
    for choices in itertools.product(*options):
        selected = dict(zip(names, choices)); needed = {r['name'] for r in reqs}; rules = list(reqs)
        expanded = set(); ok = True
        while needed - expanded:
            n = min(needed - expanded); expanded.add(n)
            v = selected.get(n)
            if v is None: ok = False; break
            more = deps.get(n, {}).get(v, [])
            rules.extend(more); needed.update(r['name'] for r in more)
        if not ok or any(selected.get(r['name']) is None or not match(selected[r['name']], r) for r in rules): continue
        if any(v is not None and n not in needed for n, v in selected.items()): continue
        key = tuple(ver(selected[n]) if selected[n] is not None else (-1, -1, -1) for n in names)
        if best_key is None or key > best_key:
            best_key = key; best = {n: selected[n] for n in names if n in needed}
    if best is None: raise ValueError('unsatisfiable')
    return best


def audit(data):
    grouped = {}
    for e in data['events']:
        key = (e['tenant'], e['event_id'], e['revision'])
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
        rules = [r for r in data['policies'] if r['tenant'] == e['tenant'] and r['start'] <= e['time'] < r['end']
                 and r['user'] in ('*', e['user']) and r['action'] in ('*', e['action'])]
        if not rules or any(r['decision'] == 'deny' for r in rules): raise ValueError('denied')
        row = users[(e['tenant'], e['user'])]; row[0] += 1; row[1] += e['amount']
    return {'version': 1, 'users': [{'tenant': t, 'user': u, 'count': v[0], 'amount': v[1]} for (t, u), v in sorted(users.items())], 'total_events': len(latest)}


def protocol(data):
    def respond(item, default):
        version = item.get('version', default)
        if version not in (1, 2): return {'status': 400, 'body': {'error': 'E_VERSION'}}
        kind = item.get('kind', 'ok')
        statuses = {'missing': 404, 'invalid': 400, 'conflict': 409}
        codes = {'missing': 1, 'invalid': 2, 'conflict': 3}
        if kind == 'ok': return {'status': 200, 'body': {'ok': True, 'error': None}}
        if kind not in codes: kind = 'invalid'
        return {'status': statuses[kind], 'body': {'ok': False, 'error': codes[kind] if version == 1 else 'E_' + kind.upper()}}
    if 'items' in data: return [respond(x, data.get('version', 1)) for x in data['items']]
    return respond(data, 1)


def parse_lines(text):
    result = {}
    for i, line in enumerate(text.splitlines(), 1):
        try:
            if '=' not in line: raise ValueError()
            key, value = line.split('=', 1)
            if not key or key in result: raise ValueError()
            def bad(x): raise ValueError(x)
            result[key] = json.loads(value, parse_constant=bad)
        except Exception as exc: raise ValueError(f'line {i}') from exc
    return result


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


def report(data):
    grouped = {}
    for e in data['events']:
        k = (e['tenant'], e['id'])
        if k not in grouped or e['revision'] > grouped[k]['revision']: grouped[k] = e
        elif e['revision'] == grouped[k]['revision'] and e != grouped[k]: raise ValueError('conflict')
    output = []
    for q in data['queries']:
        days = defaultdict(list)
        for e in grouped.values():
            if e['tenant'] != q['tenant']: continue
            t = datetime.fromisoformat(e['time'].replace('Z', '+00:00'))
            if t.tzinfo is None: raise ValueError('offset')
            day = t.astimezone(ZoneInfo(data['timezones'][q['tenant']])).date().isoformat()
            if q['start'] <= day < q['end']: days[day].append(e)
        output.append({'query_id': q['query_id'], 'days': [{'date': day, 'ids': sorted(e['id'] for e in es), 'total': sum(e['amount'] for e in es)} for day, es in sorted(days.items())]})
    return output


def metrics(data):
    groups = defaultdict(list)
    for e in data['records']:
        groups[(e['tenant'], e['record_id'])].append(e)
    accepted = []; rejected = []
    for (tenant, rid), es in sorted(groups.items()):
        valid = [e for e in es if e['source'] == 'ledger' and e['signed'] is True]
        if not valid:
            rejected.append([tenant, rid]); continue
        revision = max(e['revision'] for e in valid)
        latest = [e for e in valid if e['revision'] == revision]
        signatures = {(e['region'], e['state'], e['amount']) for e in latest}
        if len(signatures) != 1:
            rejected.append([tenant, rid]); continue
        e = latest[0]; integer(e['amount'])
        if e['region'] == 'eu' and e['state'] == 'active': accepted.append({'tenant': tenant, 'record_id': rid, 'amount': e['amount']})
    totals = defaultdict(int)
    for e in accepted: totals[e['tenant']] += e['amount']
    return {'totals': dict(sorted(totals.items())), 'records': accepted, 'rejected_record_ids': rejected}


SOLVERS = {'migration': migration, 'shipping': shipping, 'resolution': resolution, 'audit': audit,
           'protocol': protocol, 'parser': parse_lines, 'invoice': invoice, 'report': report, 'metrics': metrics}
