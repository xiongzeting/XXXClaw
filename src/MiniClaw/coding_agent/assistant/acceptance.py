"""Deterministic validation of task evidence, without an extra model call."""
from __future__ import annotations

from collections import Counter
import json
import math
import re


def strict_json(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            strict_json(item)
        return
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        for item in value.values():
            strict_json(item)
        return
    raise ValueError("comparison values must be finite JSON")


def equal(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(equal(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def pointer_tokens(path):
    if not isinstance(path, str) or (path and not path.startswith('/')):
        raise ValueError("field pointer must be empty or an RFC6901 path")
    if not path:
        return []
    tokens = []
    for token in path[1:].split('/'):
        # Reject invalid escapes instead of silently checking another field.
        if re.search(r'~(?![01])', token):
            raise ValueError("invalid JSON pointer escape")
        tokens.append(token.replace('~1', '/').replace('~0', '~'))
    return tokens


def pointer(value, path):
    for token in pointer_tokens(path):
        try:
            if isinstance(value, list):
                if not token.isascii() or not token.isdigit() or (len(token)>1 and token[0]=='0'):
                    raise ValueError("invalid array index")
                value = value[int(token)]
            elif isinstance(value, dict):
                value = value[token]
            else:
                raise ValueError("pointer crosses a scalar")
        except (KeyError, IndexError) as exc:
            raise ValueError(f"required field missing: {path}") from exc
    return value


def compare(actual, expected, operator='equals'):
    strict_json(actual)
    strict_json(expected)
    if operator == 'equals':
        return equal(actual, expected)
    if operator == 'same_members':
        if not isinstance(actual, list) or not isinstance(expected, list):
            raise ValueError("same_members requires arrays")
        # Multisets preserve duplicates; set() would silently forgive extra records.
        def key(v):
            return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return Counter(map(key, actual)) == Counter(map(key, expected))
    raise ValueError("unknown comparison operator")


def validate_comparisons(items, required_ids, resolve_actual):
    if not isinstance(items, list) or len(items)>128:
        raise ValueError("comparisons must be an array of at most 128 checks")
    observations=[]
    seen=set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("comparison must be an object")
        key=item.get('check_id')
        if not isinstance(key,str) or not key.strip() or key in seen:
            raise ValueError("comparison check_id must be unique and nonempty")
        seen.add(key)
        if 'final_pointer' in item:
            pointer_tokens(item['final_pointer'])
        if ('actual' in item) == ('actual_file' in item):
            raise ValueError("provide exactly one of actual or actual_file")
        actual=resolve_actual(item) if 'actual_file' in item else item['actual']
        passed=compare(actual,item['expected'],item.get('operator','equals'))
        observations.append({**item,'actual':actual,'passed':passed})
    if not set(required_ids).issubset(seen):
        raise ValueError("missing comparison IDs: " + ', '.join(sorted(set(required_ids)-seen)))
    return observations
