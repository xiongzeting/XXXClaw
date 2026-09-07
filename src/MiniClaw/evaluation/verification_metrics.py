"""Additive feedback counters; never infer historical error causes from prose."""

KINDS = ('plan_error', 'execution_error', 'protocol_error', 'comparison_failed')


def verification_counters(details):
    value = details.get('task_verification') or {}
    counters = {}
    if not isinstance(value, dict) or not value:
        return counters
    if value.get('pending_errors'):
        counters['verification_pending_feedback'] = 1
    if value.get('feedback_version', 0) >= 3:
        status = value.get('status')
        if status in KINDS:
            counters['verification_' + status + '_feedback'] = 1
    elif value.get('error'):
        counters['verification_legacy_unclassified_feedback'] = 1
    if value.get('error'):
        counters['verification_error_feedback'] = 1
    return counters
