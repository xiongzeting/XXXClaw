"""Shared public verification contract; execution and grading remain separate."""
from __future__ import annotations

import json

COMPARISON_SCHEMA = {
    'type': 'object', 'properties': {
        'check_id': {'type': 'string'},
        'pointer': {'type': 'string', 'description': 'Optional path INSIDE observations: for {"observations":{"net":5}} use /net. Omit to read the exact check_id key.'},
        'expected': {},
        'operator': {'type': 'string', 'enum': ['equals', 'same_members']},
        'expected_source': {'type': 'string', 'description': 'User requirement, formal input or repository test used to derive the expected value.'},
        'final_pointer': {'type': 'string'},
    }, 'required': ['check_id', 'expected', 'expected_source'], 'additionalProperties': False,
}
VERIFICATION_SCHEMA = {
    'type': 'array', 'minItems': 1, 'items': {
        'type': 'object', 'properties': {
            'criterion_id': {'type': 'string'},
            'check_ids': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Redundant; normally omit. If supplied, must exactly match comparisons[].check_id.'},
            'comparisons': {'type': 'array', 'minItems': 1, 'items': COMPARISON_SCHEMA},
            'artifacts': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Omit to reuse files registered in task_checkpoint.'},
        }, 'required': ['criterion_id'], 'additionalProperties': False,
    },
    'description': 'Preferred verification: reuse registered comparisons/artifacts with criterion_id only, or supply them here. '
                   'Emit one JSON object keyed by check_id, e.g. {"net":5}; {"observations":{"net":5}} is also accepted. '
                   'Omit pointer when the key equals check_id. Runtime supplies versions, compares values and records execution evidence. '
                   'Use actual test results, not constants. Nonzero exit never passes. '
                   'Python helper: from miniclaw_verification import emit; emit({"check_id": actual_value}). '
                   'Only when required_scenarios is registered, include observations.scenarios[criterion_id]=[{stage:"name",injected:true,observed:true}] from executed faults.',
}
LEGACY_EXAMPLE = {'task_checks': [{
    'criterion_id': '<current criterion>', 'version': 1, 'passed': True,
    'evidence': '<executed check>', 'artifacts': ['<tested file>'],
    'comparisons': [{'check_id': '<required check>', 'actual': '<observed>', 'expected': '<derived>'}],
}]}
LEGACY_DESCRIPTION = (
    'Legacy verification: emit one JSON record on stdout or stderr; separate log lines are allowed. '
    'Required shape: ' + json.dumps(LEGACY_EXAMPLE, separators=(',', ':')) + '. '
    'comparisons belongs INSIDE each task_checks item, not at the root or inside evidence. '
    'Match current IDs/versions; use real observations. Nonzero exit never passes.'
)
VERIFICATION_GUIDANCE = (
    'Verify using bash.verification with one JSON object keyed by check_id (observations wrapper optional); omit matching pointers. '
    'the runtime binds current IDs/versions and compares values. Use the tool schema, repository tests and formal inputs. '
    'For legacy bash(task_verification=true), use the exact nested schema in that tool; do not invent another layout. '
    'Use verification_rebind with the returned evidence_id to repair field bindings without rerunning a command. '
    'Keep requirements fixed while repairing verification; clear remaining work only when done. '
    'Self-written expectations are self-tests, not independent evaluation.'
)
