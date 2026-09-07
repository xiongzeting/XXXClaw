"""Read-only field binding repair; never execute a verifier a second time."""
from MiniClaw.coding_agent.tools.base import ToolResult


class VerificationRebindTool:
    name = 'verification_rebind'
    description = ('Correct observation field pointers in saved bash.verification evidence without rerunning commands. '
                   'Use a returned evidence_id. Expected values, requirements and declared artifact files stay fixed; '
                   'changed files or user instructions require fresh verification.')
    input_schema = {'type': 'object', 'properties': {
        'evidence_id': {'type': 'string'},
        'bindings': {'type': 'array', 'minItems': 1, 'maxItems': 128, 'items': {
            'type': 'object', 'properties': {'criterion_id': {'type': 'string'},
                'check_id': {'type': 'string'}, 'pointer': {'type': 'string',
                    'description': 'JSON pointer INSIDE observations, e.g. /net; expected values cannot be edited'}},
            'required': ['criterion_id', 'check_id', 'pointer'], 'additionalProperties': False}}},
        'required': ['evidence_id', 'bindings'], 'additionalProperties': False}

    def __init__(self, progress):
        self.progress = progress

    async def execute(self, arguments, cancellation_token=None):
        try:
            return self.progress.rebind_verification(**arguments)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            return ToolResult('Evidence rebind rejected; no command executed: ' + str(exc), is_error=True,
                              details={'not_started': True, 'rebind_error': str(exc)})
