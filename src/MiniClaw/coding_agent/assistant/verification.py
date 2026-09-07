"""Decode one structured verification result without treating log text as a score."""
from __future__ import annotations

import json
from .acceptance import strict_json, pointer


def decode_verification(result, record_key='task_checks'):
    if result.details.get('capture_truncated') or result.details.get('truncation'):
        raise ValueError('verification capture truncated; emit a smaller result')
    streams = ({k: result.details.get(k, '') for k in ('stdout', 'stderr')}
               if 'stdout' in result.details else {'combined': result.content})
    candidates = []
    def unique_keys(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate JSON key')
            value[key] = item
        return value
    decoder = json.JSONDecoder(object_pairs_hook=unique_keys)
    for source, text in streams.items():
        # Only whole top-level JSON records starting on a line are candidates.
        # Ordinary logs may precede/follow them; never scan nested values for success.
        offset = 0
        while offset < len(text):
            end = text.find('\n', offset)
            end = len(text) if end < 0 else end
            line = text[offset:end].lstrip()
            start = offset + len(text[offset:end]) - len(line)
            if line.startswith(('{', '[')):
                try:
                    payload, length = decoder.raw_decode(text[start:])
                except ValueError as exc:
                    raise ValueError('malformed structured JSON record') from exc
                else:
                    offset = start + length
                    tail_end = text.find('\n', offset)
                    if text[offset:len(text) if tail_end < 0 else tail_end].strip():
                        raise ValueError('JSON record must occupy whole lines')
                    if record_key == 'observations' or (isinstance(payload, dict) and record_key in payload):
                        strict_json(payload)
                        candidates.append((source, payload))
                    continue
            offset = end + 1
    if len(candidates) != 1:
        raise ValueError(f'expected exactly one {record_key} JSON record, found {len(candidates)}')
    source, payload = candidates[0]
    if record_key == 'observations':
        if not isinstance(payload, dict) or 'task_checks' in payload:
            raise ValueError('Expected one observation object keyed by check_id; task_checks uses the legacy interface')
        if 'observations' in payload:
            if set(payload) - {'observations', 'protocol'}:
                raise ValueError('Ambiguous observation wrapper: do not mix outer fields and observations; emit one check_id object')
            if not isinstance(payload['observations'], dict):
                raise ValueError('observations must be an object keyed by check_id')
        else:
            result.details.setdefault('verification_normalizations', []).append({'from': 'bare_object', 'to': 'observations'})
            payload = {'observations': payload}
    if payload.get('protocol', 'miniclaw-task-checks-v1') != 'miniclaw-task-checks-v1':
        raise ValueError('unsupported task verification protocol')
    return payload, source


def observation_value(observations, comparison, normalizations):
    """Resolve an explicit binding; repair only an unambiguous wrapper prefix."""
    path = comparison.get('pointer')
    if path is None:
        key = comparison['check_id']
        if key not in observations:
            raise ValueError(f'Missing observation key {key!r}; available keys: {list(observations)[:16]}. '
                             'Set pointer to an existing field inside the observation object; expected is unchanged')
        return observations[key]
    candidates = []
    for candidate in [path, *([path[len('/observations'):]] if path.startswith('/observations/') else [])]:
        try:
            candidates.append((candidate, pointer(observations, candidate)))
        except (ValueError, TypeError, KeyError):
            pass
    if len(candidates) > 1:
        raise ValueError(f'Ambiguous observation pointer {path}: both inner and wrapper-relative paths exist. '
                         'Omit pointer to select an exact check_id key, or emit distinct observation names')
    if not candidates:
        raise ValueError(f'required field missing: {path}; pointer is INSIDE observations, '
                         f'available keys: {list(observations)[:16]}. '
                         'For {"observations":{"net":5}} use /net; use verification_rebind for an existing field')
    resolved, value = candidates[0]
    if resolved != path:
        normalizations.append({'check_id': comparison['check_id'], 'from': path, 'to': resolved})
    return value


def validate_evidence(value):
    if isinstance(value, str) and value.strip():
        return
    if isinstance(value, dict) and value:
        strict_json(value)
        return
    raise ValueError('evidence must be a nonempty string or JSON object')
