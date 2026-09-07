"""Small stdlib-only writer available to Python checks in Host and Docker.

It serializes observations; it never creates passing values or executes tests.
Use emit({'total': calculated_total}) after running checks. Diagnostics belong
on stderr or in separate commands, never appended to the JSON line.
"""
import json
import sys

_emitted = False


def emit(observations, *, scenarios=None):
    global _emitted
    if _emitted:
        raise ValueError('Emit one combined verification record per process')
    if not isinstance(observations, dict) or not all(isinstance(k, str) and k for k in observations):
        raise ValueError('Observations must be a dictionary keyed by check_id')
    data = dict(observations)
    if scenarios is not None:
        if 'scenarios' in data or not isinstance(scenarios, dict):
            raise ValueError('scenarios must map criterion_id to observed scenarios, without duplicate fields')
        data['scenarios'] = scenarios
    record = json.dumps({'observations':data}, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    print(record, file=sys.stdout, flush=True)
    _emitted = True
