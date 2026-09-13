import copy,json
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
    for name in []:
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
                         **{}}
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

solve=migration
