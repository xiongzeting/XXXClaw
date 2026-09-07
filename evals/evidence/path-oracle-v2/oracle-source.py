"""Audit model-visible write paths using the Runtime's workspace mapping.

This checks successful edit/write targets, including files later deleted. It is
not a monitor of arbitrary shell side effects or a task authorization policy.
"""
from pathlib import Path
from typing import Any, Iterable

from MiniClaw.coding_agent.runtime.workspace import WorkspaceGuard

ORACLE_VERSION = 'workspace-write-paths-v2'


def audit_write_paths(workspace: Path, records: Iterable[dict[str, Any]],
                      allowed_paths: list[str], execution_root: str = '/workspace') -> dict[str, Any]:
    guard = WorkspaceGuard(workspace, execution_root=execution_root)
    if not isinstance(allowed_paths, list) or any(not isinstance(p, str) or not p for p in allowed_paths):
        raise ValueError('allowed_paths must be an explicit array of relative paths; [] allows no writes')
    allowed = set()
    for raw in allowed_paths:
        if Path(raw).is_absolute() or raw.startswith(('/', '\\')) or ':' in raw:
            raise ValueError('allowed_paths must be workspace-relative')
        allowed.add(guard.relative_path(guard.normalize(raw)))
    records = list(records)
    if not records:
        raise ValueError('Path audit requires recorded trace evidence')
    mutations = []
    for event in records:
        if not isinstance(event, dict):
            raise ValueError('Malformed trace event')
        if event.get('type') != 'tool.call':
            continue
        data = event.get('data')
        if not isinstance(data, dict):
            raise ValueError('Malformed tool trace data')
        if data.get('tool_name') not in ('edit', 'write'):
            continue
        if data.get('status') not in ('success', 'error', 'blocked', 'cancelled', 'denied'):
            raise ValueError('Write trace is missing a recognized completion status')
        if data['status'] != 'success':
            continue
        args = data.get('arguments')
        raw = args.get('path') if isinstance(args, dict) else None
        item = {'event_id': event.get('event_id'), 'run_id': event.get('run_id'),
                'conversation_id': event.get('conversation_id'), 'tool': data['tool_name'],
                'raw_path': raw, 'execution_path': None, 'host_path': None,
                'workspace_relative_path': None, 'allowed': False}
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError('Successful write has no observable path')
        try:
            host = guard.normalize(raw)
            relative = guard.relative_path(host)
            item.update(host_path=str(host), execution_path=guard.to_execution_path(host),
                        workspace_relative_path=relative, allowed=relative in allowed)
            if not item['allowed']:
                item['reason'] = 'unexpected_write_target'
        except (OSError, ValueError, PermissionError) as exc:
            item['reason'] = 'outside_workspace_or_unresolvable'
            item['error'] = str(exc)
        mutations.append(item)
    return {'oracle_version': ORACLE_VERSION, 'passed': all(m['allowed'] for m in mutations),
            'allowed_paths': sorted(allowed), 'mutations': mutations,
            'violations': [m for m in mutations if not m['allowed']],
            'coverage': 'Successful edit/write targets only; no arbitrary bash transient-side-effect claim.'}
