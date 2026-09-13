import json
from collections import defaultdict
def integer(x, minimum=0):
    if type(x) is not int or x < minimum:
        raise ValueError('integer')
    return x

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

parse=parse_lines
