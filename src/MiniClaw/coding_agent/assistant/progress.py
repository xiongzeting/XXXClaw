"""Session-local checkpoints and evidence-backed acceptance tracking."""
from __future__ import annotations

import json
import os
import hashlib
import copy
from collections import Counter
from pathlib import Path

from MiniClaw.coding_agent.tools.base import ToolResult
from MiniClaw.coding_agent.runtime.workspace import WorkspaceGuard
from .acceptance import compare, pointer, pointer_tokens, strict_json, validate_comparisons
from .verification import decode_verification, validate_evidence, observation_value
from .verification_evidence import VerificationEvidenceStore
from MiniClaw.coding_agent.verification_contract import VERIFICATION_GUIDANCE, COMPARISON_SCHEMA


class TaskProgress:
    def __init__(self, session_dir: Path, workspace: Path | None = None, guard: WorkspaceGuard | None = None):
        self.path = session_dir / "task-progress.json"
        self.workspace = workspace
        self.guard = guard
        self.evidence_store = VerificationEvidenceStore(session_dir)
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        if not isinstance(self.state, dict):
            raise ValueError("Invalid task progress: expected an object")
        # Old sessions remain resumable; new calls use stable IDs and versions.
        # Existing recovery contracts retain their old obligations; new contracts
        # enumerate applicable scenarios instead of inheriting four unrelated ones.
        old_criteria = self.state.get('criteria', [])
        for item in old_criteria:
            if isinstance(item, dict) and item.get('kind') == 'recovery':
                item.setdefault('required_scenarios', ['before_commit', 'during_commit', 'response_lost', 'retry'])
        self.state["criteria"] = [self._criterion(item) for item in old_criteria]

    @staticmethod
    def _criterion(item):
        if isinstance(item, str):
            return {"criterion_id": item, "description": item, "version": 1, "legacy": True}
        if not isinstance(item, dict):
            raise ValueError("criterion must be an object")
        value = dict(item)
        if not all(isinstance(value.get(k), str) and value[k].strip() for k in ("criterion_id", "description")):
            raise ValueError("criterion_id and description must be non-empty strings")
        if type(value.get("version")) is not int or value["version"] < 1:
            raise ValueError("criterion version must be a positive integer")
        kind = value.get("kind", "behavior")
        if kind not in {"behavior", "data", "recovery"}:
            raise ValueError("criterion kind must be behavior, data or recovery")
        ids = value.get("check_ids", [])
        if not isinstance(ids,list) or not all(isinstance(k,str) and k.strip() for k in ids) or len(set(ids)) != len(ids):
            raise ValueError("check_ids must be unique nonempty strings")
        if kind in {"data", "recovery"} and not ids:
            raise ValueError("data/recovery criteria require explicit check_ids")
        if 'comparisons' in value:
            from MiniClaw.coding_agent.tools.executor import ToolExecutor
            ToolExecutor._validate_value(value['comparisons'], {'type':'array','minItems':1,'items':COMPARISON_SCHEMA}, 'criterion.comparisons')
            declared = [c['check_id'] for c in value['comparisons']]
            if len(set(declared)) != len(declared) or not set(ids).issubset(declared):
                raise ValueError('Registered comparisons must uniquely cover required check_ids')
            for c in value['comparisons']:
                strict_json(c['expected'])
                if not c['expected_source'].strip():
                    raise ValueError('Registered expected_source must identify a requirement or reference test')
                for field in ('pointer', 'final_pointer'):
                    if field in c:
                        pointer_tokens(c[field])
        if 'artifacts' in value and (not isinstance(value['artifacts'], list) or
                not all(isinstance(p, str) and p for p in value['artifacts'])):
            raise ValueError('Registered artifacts must be file paths')
        stages = value.setdefault('required_scenarios', [])
        if (not isinstance(stages, list) or not all(isinstance(s, str) and s.strip() for s in stages)
                or len(stages) != len(set(stages)) or len(stages) > 32):
            raise ValueError('required_scenarios must be up to 32 unique scenario names from the task requirements')
        return value

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def begin_user_turn(self, prompt):
        revision = self.state.get('user_revision', 0) + 1
        if self.state.get('status') == 'completed':
            self.state = {'criteria': []}
        self.state['user_revision'] = revision
        self.state['user_message_sha256'] = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
        self.state.pop('verification_retry', None)
        self.state.pop('verification_protocol_valid', None)
        self.save()

    def checkpoint(self, summary, remaining, next_action, criteria, withdrawn=None, requirement_revision=None):
        if self.state.get("status") == "completed":
            self.state = {k: self.state[k] for k in ('user_revision', 'user_message_sha256') if k in self.state}
        updated = {item["criterion_id"]: item for item in self.state.get("criteria", [])}
        verified = dict(self.state.get("verified", {}))
        changes = []
        seen = set()
        for raw in criteria:
            raw = dict(raw) if isinstance(raw, dict) else raw
            if isinstance(raw, dict):
                if not raw.get('criterion_id'):
                    matches = [c for c in updated.values() if c['description'].strip() == str(raw.get('description', '')).strip()
                               and c.get('kind', 'behavior') == raw.get('kind', 'behavior')]
                    if len(matches) > 1:
                        raise ValueError('Ambiguous existing requirement; use its criterion_id')
                    raw['criterion_id'] = matches[0]['criterion_id'] if matches else f"requirement-{len(updated) + 1}"
                    if not matches:
                        while raw['criterion_id'] in updated:
                            raw['criterion_id'] += '-new'
                prior = updated.get(raw['criterion_id'])
                raw.setdefault('version', prior['version'] if prior else 1)
                if prior:
                    raw.setdefault('kind', prior.get('kind', 'behavior'))
                    raw.setdefault('check_ids', prior.get('check_ids', []))
                    raw.setdefault('required_scenarios', prior.get('required_scenarios', []))
                    for field in ('comparisons', 'artifacts'):
                        if field in prior:
                            raw.setdefault(field, copy.deepcopy(prior[field]))
                elif any(raw['criterion_id'] in c.get('check_ids', []) for c in updated.values()):
                    raise ValueError('This ID is already a comparison within an existing criterion; submit observations under that criterion')
                if raw.get('kind') in {'data', 'recovery'}:
                    raw.setdefault('check_ids', [c['check_id'] for c in raw['comparisons']] if raw.get('comparisons') else [raw['criterion_id']])
            item = self._criterion(raw)
            for path in item.get('artifacts', []):
                self._artifact_path(path)
            key = item["criterion_id"]
            if key in seen:
                raise ValueError("duplicate criterion_id")
            seen.add(key)
            old = updated.get(key)
            changed = old and any(not compare(old.get(k, default), item.get(k, default), 'equals') for k, default in
                                  [('description', ''), ('kind', 'behavior'), ('check_ids', []), ('required_scenarios', []),
                                   ('comparisons', None), ('artifacts', None)])
            revised = old and (changed or item['version'] != old['version'])
            if revised:
                self._require_user_revision(old, requirement_revision)
                item['version'] = old['version'] + 1
            item['requirement_revision'] = (self.state.get('user_revision', 0) if not old or revised
                                             else old.get('requirement_revision', 0))
            if old and item["version"] < old["version"]:
                raise ValueError("criterion version cannot decrease")
            if (old and item["version"] == old["version"] and
                    (old.get("kind","behavior") != item.get("kind","behavior") or old.get("check_ids",[]) != item.get("check_ids",[]))):
                raise ValueError("changing verification requirements must increment version")
            if old != item:
                changes.append({"action": "update" if old else "add", "before": old, "after": item})
            if old and item["version"] != old["version"]:
                verified.pop(key, None)
            updated[key] = item
        for key in withdrawn or []:
            if key in seen or key not in updated:
                raise ValueError("withdrawn IDs must exist and cannot also be updated")
            self._require_user_revision(updated[key], requirement_revision)
            changes.append({"action": "withdraw", "before": updated.pop(key)})
            verified.pop(key, None)
        self.state.update(
            status="active", summary=summary, remaining=remaining, next_action=next_action,
            criteria=list(updated.values()), verified=verified,
        )
        self.state.setdefault("criterion_changes", []).extend(changes)
        for change in changes:
            if (change['action'] == 'withdraw' or (change['action'] == 'update' and
                    change['before']['version'] != change['after']['version'])):
                key = change['before']['criterion_id']
                for field in ('verification_errors', 'failed_checks', 'failed_observations'):
                    self.state.get(field, {}).pop(key, None)
        if not self.state.get('verification_errors'):
            self.state.pop('verification_error', None)
        self.save()

    def _require_user_revision(self, item, revision):
        current = self.state.get('user_revision', 0)
        if (type(revision) is not int or revision != current or
                current <= item.get('requirement_revision', 0)):
            raise ValueError('Requirement changes/withdrawal need a newer user message and its requirement_revision; '
                             'verification formatting does not change a requirement')

    def prepare_verification(self, specifications):
        criteria = {c['criterion_id']: c for c in self.state.get('criteria', [])}
        if not isinstance(specifications, list) or not specifications:
            raise ValueError('verification must contain at least one criterion')
        plan = []; seen = set()
        for spec in specifications:
            spec = copy.deepcopy(spec)
            key = spec['criterion_id']
            if key in seen or key not in criteria:
                raise ValueError('verification criterion_id must be current and unique: ' + str(key))
            seen.add(key); criterion = criteria[key]
            for field in ('comparisons', 'artifacts'):
                if field not in spec:
                    if field not in criterion:
                        raise ValueError(f'{key}: register {field} once in task_checkpoint or supply verification.{field}')
                    spec[field] = copy.deepcopy(criterion[field])
            if 'comparisons' in criterion:
                canonical = lambda values: [{k:v for k,v in c.items() if k != 'pointer'} for c in values]
                if not compare(canonical(spec['comparisons']), canonical(criterion['comparisons'])):
                    raise ValueError('Registered expectations stay fixed; only field pointers may be corrected')
            if 'artifacts' in criterion and ({str(self._artifact_path(p)) for p in spec['artifacts']} !=
                                             {str(self._artifact_path(p)) for p in criterion['artifacts']}):
                raise ValueError('Registered artifact scope stays fixed; reuse it or revise the requirement')
            checks = spec['comparisons']
            if not isinstance(checks, list) or not 1 <= len(checks) <= 128:
                raise ValueError('verification comparisons must contain 1..128 observations')
            ids = [c['check_id'] for c in checks]
            if len(set(ids)) != len(ids) or not all(isinstance(i, str) and i.strip() for i in ids):
                raise ValueError('comparison IDs must be nonempty and unique')
            if 'check_ids' in spec and spec['check_ids'] != ids:
                raise ValueError('verification.check_ids is redundant: omit it, or match comparisons[].check_id exactly')
            if not set(criterion.get('check_ids', [])).issubset(ids):
                raise ValueError('verification plan is missing required check IDs: ' +
                                 ', '.join(sorted(set(criterion.get('check_ids', [])) - set(ids))))
            for check in checks:
                if 'pointer' in check:
                    pointer_tokens(check['pointer'])
                strict_json(check['expected'])
                if check.get('operator', 'equals') not in {'equals', 'same_members'}:
                    raise ValueError('unknown comparison operator')
                if not isinstance(check.get('expected_source'), str) or not check['expected_source'].strip():
                    raise ValueError('expected_source must identify the requirement/input/test')
                if 'final_pointer' in check:
                    pointer_tokens(check['final_pointer'])
            if not isinstance(spec['artifacts'], list):
                raise ValueError('artifacts must be an array')
            for path in spec['artifacts']:
                self._artifact_path(path)
            entry = {**copy.deepcopy(spec), 'version': criterion['version']}
            if 'check_ids' in entry:
                entry.pop('check_ids')
                entry['binding_normalizations'] = ['redundant check_ids matched comparisons and was omitted']
            plan.append(entry)
        return {'protocol': 1, 'checks': plan}

    def _verification_payload(self, result):
        plan = result.details.get('verification_plan')
        if plan is None:
            payload, source = decode_verification(result)
            self._normalize_legacy(payload, result)
            return payload, source
        payload, source = decode_verification(result, 'observations')
        observations = payload['observations']
        checks = []
        for spec in plan['checks']:
            item = {**spec, 'passed': True,
                           'evidence': {'source': 'executed_command', 'verifier_sha256': result.details['verifier_sha256'],
                                        'expectations': 'declared self-test; not independent eval'}}
            try:
                item['comparisons'] = [{**c, 'actual': observation_value(observations, c,
                    result.details.setdefault('verification_normalizations', []))} for c in spec['comparisons']]
                expected = next((c for c in self.state['criteria'] if c['criterion_id'] == spec['criterion_id']), None)
                if expected is None:
                    raise ValueError('Verification criterion was withdrawn; old evidence cannot satisfy it')
                if expected.get('required_scenarios'):
                    scenarios = observations.get('scenarios', {})
                    if not isinstance(scenarios, dict):
                        raise ValueError('observations.scenarios must map criterion_id to scenario arrays')
                    item['scenarios'] = scenarios.get(spec['criterion_id'], [])
            except (ValueError, TypeError, KeyError) as exc:
                item['runtime_error'] = str(exc)
            checks.append(item)
        return {'task_checks': checks}, source

    def _normalize_legacy(self, payload, result):
        """Move uniquely identified legacy observations; never invent evidence."""
        items = payload.get('task_checks')
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            return
        required = {c['criterion_id']: set(c.get('check_ids', [])) for c in self.state.get('criteria', [])}
        moves = []
        def attach(item, comparison, origin):
            if not isinstance(comparison, dict):
                raise ValueError('comparison must be an object')
            key = comparison.get('check_id')
            current = item.setdefault('comparisons', [])
            if not isinstance(current, list) or any(c.get('check_id') == key for c in current if isinstance(c, dict)):
                raise ValueError('ambiguous duplicate comparison across layouts')
            current.append(comparison)
            moves.append({'check_id': key, 'from': origin, 'to': item.get('criterion_id', item.get('criterion'))})
        for item in items:
            if 'recovery' in item:
                if 'scenarios' in item:
                    item['runtime_error'] = 'Ambiguous scenario fields: provide scenarios only, not both recovery and scenarios'
                else:
                    item['scenarios'] = item.pop('recovery')
                    moves.append({'from': 'recovery', 'to': 'scenarios', 'criterion_id': item.get('criterion_id')})
            evidence = item.get('evidence')
            if isinstance(evidence, dict) and 'comparisons' in evidence:
                nested = evidence['comparisons']
                if isinstance(nested, dict):
                    nested = [{**c, 'check_id': key} for key, c in nested.items()
                              if isinstance(c, dict) and c.get('check_id', key) == key]
                    if len(nested) != len(evidence['comparisons']):
                        raise ValueError('ambiguous evidence.comparisons mapping')
                if not isinstance(nested, list):
                    raise ValueError('evidence.comparisons must be an array or ID mapping')
                for comparison in nested:
                    attach(item, comparison, 'evidence.comparisons')
        if 'comparisons' in payload:
            root = payload['comparisons']
            if not isinstance(root, list):
                raise ValueError('root comparisons must be an array')
            for comparison in root:
                key = comparison.get('check_id') if isinstance(comparison, dict) else None
                owners = [i for i in items if key in required.get(i.get('criterion_id', i.get('criterion')), set())]
                if len(owners) != 1:
                    raise ValueError('root comparison cannot be uniquely bound to a required criterion: ' + str(key))
                attach(owners[0], comparison, 'root.comparisons')
        if moves:
            result.details['verification_normalizations'] = moves

    def _artifact_path(self, value):
        if not self.workspace or not isinstance(value, str) or not value:
            raise ValueError("artifact requires a workspace file path")
        guard = self.guard or WorkspaceGuard(self.workspace, execution_root="/workspace")
        return guard.resolve(value, access="read")

    def _fingerprints(self, artifacts):
        if not isinstance(artifacts, list):
            raise ValueError("artifacts must be an array")
        result = {}
        for value in artifacts:
            path = self._artifact_path(value)
            if not path.is_file():
                raise ValueError("verified artifact must exist")
            result[path.relative_to(self.workspace.resolve()).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def _capture_verification(self, call, result):
        plan = result.details.get('verification_plan')
        if not plan or result.is_error or result.details.get('not_started') or result.details.get('exit_code') != 0:
            return
        try:
            # Rebinding selects fields in an existing record; it cannot repair absent output.
            decode_verification(copy.deepcopy(result), 'observations')
            if any(not s.get('artifacts') for s in plan['checks']):
                raise ValueError('Each criterion needs declared artifact files for safe evidence reuse')
            manifest = self._fingerprints([p for s in plan['checks'] for p in s['artifacts']])
            capture = {'plan': plan, 'artifacts': manifest,
                'user_revision': self.state.get('user_revision', 0), 'original_call_id': call.call_id,
                'verifier_sha256': result.details['verifier_sha256'],
                'stdout': result.details.get('stdout', result.content), 'stderr': result.details.get('stderr', '')}
            identifier, digest = self.evidence_store.save(capture)
            known = self.state.setdefault('verification_evidence', {})
            known[identifier] = digest
            while len(known) > 16:
                del known[next(iter(known))]
            result.details['evidence_id'] = identifier
        except (ValueError, TypeError, KeyError, OSError) as exc:
            result.details['evidence_reuse_unavailable'] = str(exc)

    def rebind_verification(self, evidence_id, bindings):
        known = self.state.get('verification_evidence', {})
        if evidence_id not in known:
            raise ValueError('Unknown or expired evidence_id for this session')
        capture = self.evidence_store.load(evidence_id, known[evidence_id])
        if capture['user_revision'] != self.state.get('user_revision', 0):
            raise ValueError('User instructions changed; execute fresh verification')
        criteria = {c['criterion_id']: c for c in self.state['criteria']}
        plan = copy.deepcopy(capture['plan'])
        for spec in plan['checks']:
            if spec['criterion_id'] not in criteria or criteria[spec['criterion_id']]['version'] != spec['version']:
                raise ValueError('Requirement changed or withdrawn; execute fresh verification')
        if not capture['artifacts'] or self._fingerprints(list(capture['artifacts'])) != capture['artifacts']:
            raise ValueError('Verified artifacts changed; execute fresh verification')
        if not isinstance(bindings, list) or not 1 <= len(bindings) <= 128:
            raise ValueError('bindings must contain 1..128 field bindings')
        checks = {(s['criterion_id'], c['check_id']): c for s in plan['checks'] for c in s['comparisons']}
        seen = set()
        for binding in bindings:
            if not isinstance(binding, dict) or set(binding) != {'criterion_id', 'check_id', 'pointer'}:
                raise ValueError('Rebinding accepts only criterion_id, check_id and pointer; expectations stay fixed')
            key = (binding['criterion_id'], binding['check_id'])
            if key not in checks or key in seen:
                raise ValueError('Binding must name a unique check from the captured plan')
            seen.add(key)
            pointer_tokens(binding['pointer'])
            checks[key]['pointer'] = binding['pointer']
        return ToolResult('Reparsed saved command evidence; no command was executed in this call.', details={
            'exit_code': 0, 'stdout': capture['stdout'], 'stderr': capture['stderr'],
            'verification_plan': plan, 'evidence_id': evidence_id, 'evidence_reused': True,
            'not_started': True, 'original_call_id': capture['original_call_id'],
            'verifier_sha256': capture['verifier_sha256'], 'corrected_bindings': copy.deepcopy(bindings)})

    def _invalidate(self, call, result):
        verified = self.state.setdefault("verified", {})
        if result.details.get('not_started'):
            return
        if call.name not in {"write", "edit", "bash"}:
            return
        changes = result.details.get("workspace_changes", {})
        if changes.get("complete") is True:
            paths = changes.get("changed_paths", [])
        elif call.name in {"write", "edit"} and not result.is_error and call.arguments.get("path"):
            try:
                path = self._artifact_path(call.arguments["path"])
                paths = [path.relative_to(self.workspace.resolve()).as_posix()]
            except (ValueError, OSError):
                paths = None
        else:
            paths = None
        if paths == []:
            return
        self.state['workspace_epoch'] = self.state.get('workspace_epoch', 0) + 1
        for key, evidence in list(verified.items()):
            covered = evidence.get("artifacts", {})
            if paths is None or not covered or any(p in covered for p in paths):
                verified.pop(key, None)

    def observe(self, call, result):
        if not self.state:
            return
        self._invalidate(call, result)
        if call.name != 'verification_rebind' and (call.name != 'bash' or not (
                call.arguments.get('task_verification') is True or 'verification' in call.arguments)):
            self.save()
            return
        if call.name == 'bash':
            self._capture_verification(call, result)
        errors = self.state.setdefault('verification_errors', {})
        current_errors = {}
        error_category = 'protocol_error'
        attempted = set()
        progress_before = set(self.state.get('verification_protocol_valid', []))
        valid = set(progress_before)
        def record_transport_error(message):
            plan = result.details.get('verification_plan', {})
            keys = [s['criterion_id'] for s in plan.get('checks', [])]
            if not keys:
                requested = call.arguments.get('verification', [])
                known = {c['criterion_id'] for c in self.state['criteria']}
                if isinstance(requested, list):
                    keys = [s['criterion_id'] for s in requested if isinstance(s, dict)
                            and isinstance(s.get('criterion_id'), str) and s['criterion_id'] in known]
            if keys:
                for key in keys:
                    errors[key] = message
                    current_errors[key] = message
                    attempted.add(key)
            else:
                errors['_transport'] = message
                current_errors['_transport'] = message
                self.state['verification_transport_pending'] = [c['criterion_id'] for c in self.state['criteria']]
        # Older durable state had only a summary. Its affected criteria remain pending.
        self.state.pop('verification_error', None)
        if result.is_error or result.details.get('exit_code') != 0:
            error_category = ('plan_error' if result.details.get('not_started') or
                              result.details.get('verification_plan_error') else
                              'protocol_error' if call.name == 'verification_rebind' else 'execution_error')
            record_transport_error(result.details.get('verification_plan_error') or
                result.details.get('rebind_error') or (result.content[:1800] if result.details.get('not_started')
                    else 'Verification command did not exit successfully: ' +
                    str(result.details.get('exit_code')) + '; ' +
                    (result.details.get('stderr') or result.content)[-1200:]))
        else:
            try:
                payload, source = self._verification_payload(result)
                result.details['verification_transport'] = {'protocol': 'miniclaw-task-checks-v1', 'source': source}
                checks = payload['task_checks']
                if not isinstance(checks, list) or not checks:
                    raise ValueError('task_checks must be a nonempty array')
                criteria = {c['criterion_id']: c for c in self.state.get('criteria', [])}
                counts = Counter(item.get('criterion_id', item.get('criterion')) for item in checks
                                 if isinstance(item, dict) and isinstance(item.get('criterion_id', item.get('criterion')), str))
                for index, item in enumerate(checks):
                    key = item.get('criterion_id', item.get('criterion')) if isinstance(item, dict) else None
                    error_key = key if isinstance(key, str) and key in criteria else '_transport'
                    # Rechecking one criterion can invalidate that criterion only.
                    if error_key != '_transport':
                        attempted.add(key)
                        self.state.setdefault('verified', {}).pop(key, None)
                        self.state.setdefault('failed_checks', {}).pop(key, None)
                        self.state.setdefault('failed_observations', {}).pop(key, None)
                    try:
                        if not isinstance(key, str) or key not in criteria:
                            raise ValueError('unknown criterion_id')
                        if counts[key] != 1:
                            raise ValueError('invalid or duplicate criterion')
                        self._observe_check(call, result, item, criteria[key])
                        errors.pop(key, None)
                        valid.add(json.dumps([self.state.get('user_revision', 0), key, criteria[key]['version']]))
                        pending = self.state.get('verification_transport_pending', [])
                        if key in pending:
                            pending.remove(key)
                        if not pending:
                            errors.pop('_transport', None)
                    except (ValueError, TypeError, KeyError, OSError) as exc:
                        message = f'task_checks[{index}] ({key}): {exc}'
                        if error_key == '_transport':
                            record_transport_error(message)
                        else:
                            errors[error_key] = message
                            current_errors[error_key] = message
            except (ValueError, TypeError, KeyError, OSError) as exc:
                record_transport_error(str(exc))
        self.state['verification_protocol_valid'] = sorted(valid)
        if errors:
            self.state['verification_error'] = 'Invalid verification: ' + '; '.join(dict.fromkeys(errors.values()))[:1400]
            previous = self.state.get('verification_retry', {})
            self.state['verification_retry'] = {'basis': 'unresolved_protocol_without_new_valid_criterion',
                'count': 1 if valid - progress_before else previous.get('count', 0) + 1}
        else:
            self.state.pop('verification_retry', None)
        result.details['verification_current'] = {
            'error_category': error_category if current_errors else None,
            'error': ('Invalid verification: ' + '; '.join(dict.fromkeys(current_errors.values()))[:1800]) if current_errors else None,
            'errors': current_errors, 'attempted_criteria': sorted(attempted),
            'pending_errors': {k: v for k, v in errors.items() if k not in current_errors},
            'failed_observations': {k: v for k, v in self.state.get('failed_observations', {}).items() if k in attempted},
        }
        self.save()

    def _observe_check(self, call, result, item, expected):
        key = expected['criterion_id']
        if item.get('runtime_error'):
            raise ValueError(item['runtime_error'])
        version = item.get('version', 1 if expected.get('legacy') else None)
        if type(version) is not int or version != expected['version']:
            raise ValueError('verification version must match current criterion')
        if type(item['passed']) is not bool:
            raise ValueError('passed must be a boolean')
        validate_evidence(item['evidence'])
        # Legacy JSON and rebound evidence must obey the same registered contract.
        if 'comparisons' in expected:
            observed = {c.get('check_id'): c for c in item.get('comparisons', []) if isinstance(c, dict)}
            for registered in expected['comparisons']:
                candidate = observed.get(registered['check_id'], {})
                for field, default in (('expected', None), ('operator', 'equals'), ('final_pointer', None)):
                    if not compare(candidate.get(field, default), registered.get(field, default)):
                        raise ValueError('Registered expectations stay fixed: ' + registered['check_id'])
        if 'artifacts' in expected:
            declared_paths = {str(self._artifact_path(p)) for p in expected['artifacts']}
            actual_paths = {str(self._artifact_path(p)) for p in item.get('artifacts', [])}
            if declared_paths != actual_paths:
                raise ValueError('Registered artifact scope stays fixed')
        actual_files = []; observed_hashes = {}
        def resolve_actual(comparison):
            path = self._artifact_path(comparison['actual_file'])
            if path.stat().st_size > 1024 * 1024:
                raise ValueError('JSON verification file exceeds 1 MiB')
            actual_files.append(comparison['actual_file'])
            raw = path.read_bytes()
            observed_hashes[path.relative_to(self.workspace.resolve()).as_posix()] = hashlib.sha256(raw).hexdigest()
            return pointer(json.loads(raw.decode('utf-8-sig')), comparison.get('pointer', ''))
        comparisons = validate_comparisons(item.get('comparisons', []), expected.get('check_ids', []), resolve_actual)
        if expected.get('required_scenarios'):
            scenarios = item.get('scenarios', [])
            if not isinstance(scenarios, list) or not all(isinstance(s, dict) for s in scenarios):
                raise ValueError('recovery scenarios must be objects')
            stages = {s.get('stage') for s in scenarios if s.get('injected') is True and s.get('observed') is True}
            missing = set(expected['required_scenarios']) - stages
            if missing:
                raise ValueError('Missing executed scenarios: ' + ', '.join(sorted(missing)) +
                    '. Legacy: task_checks item.scenarios=[{stage:"name",injected:true,observed:true}]. '
                    'Structured: observations.scenarios[criterion_id]=the same array. Do not claim unexecuted faults.')
        if item['passed'] and all(c['passed'] for c in comparisons):
            artifacts = item.get('artifacts', [])
            if not isinstance(artifacts, list):
                raise ValueError('artifacts must be an array')
            fingerprints = self._fingerprints([*artifacts, *actual_files])
            if any(fingerprints.get(k) != v for k, v in observed_hashes.items()):
                raise ValueError('output changed during verification')
            self.state.setdefault('verified', {})[key] = {
                'call_id': result.details.get('original_call_id', call.call_id),
                'rebind_call_id': call.call_id if result.details.get('evidence_reused') else None,
                'evidence_id': result.details.get('evidence_id'), 'evidence': item['evidence'], 'version': version,
                'artifacts': fingerprints, 'comparisons': comparisons, 'scenarios': item.get('scenarios', []),
                'verifier_sha256': result.details.get('verifier_sha256') or hashlib.sha256(str(call.arguments.get('command', '')).encode()).hexdigest(),
            }
        else:
            failed = [c for c in comparisons if not c['passed']]
            self.state.setdefault('failed_checks', {})[key] = [c['check_id'] for c in failed] or ['command_assertion']
            self.state.setdefault('failed_observations', {})[key] = [
                {'check_id': c['check_id'], 'actual': self._brief(c['actual']), 'expected': self._brief(c['expected'])}
                for c in failed[:8]] or [{'check_id': 'command_assertion', 'actual': False, 'expected': True}]

    @staticmethod
    def _brief(value):
        text = json.dumps(value, ensure_ascii=False)
        return value if len(text) <= 400 else text[:400] + '… (full evidence in Trace)'

    def pending(self):
        verified = self.state.get("verified", {})
        for key, evidence in list(verified.items()):
            for path, digest in evidence.get("artifacts", {}).items():
                try:
                    current = hashlib.sha256(self._artifact_path(path).read_bytes()).hexdigest()
                except (ValueError, OSError):
                    current = None
                if current != digest:
                    verified.pop(key, None)
                    break
        return [item["description"] for item in self.state.get("criteria", []) if item["criterion_id"] not in verified]

    def next_step(self):
        if self.state.get('verification_error'):
            return '修复验证命令或验收协议，不更改需求编号或版本：' + self.state['verification_error']
        if self.state.get('failed_observations'):
            return '修复实际失败项：' + json.dumps(self.state['failed_observations'], ensure_ascii=False)[:1200]
        remaining = self.state.get("remaining", [])
        if remaining:
            return "继续未完成工作：" + remaining[0]
        pending = self.pending()
        if pending:
            return "验证尚未通过的要求：" + pending[0]
        return "核对最后工具结果并交付；已有检查通过，不要重复实现或写入。"

    def final_blocker(self):
        if self.state.get("status") == "completed":
            return None
        pending = self.pending()
        remaining = self.state.get("remaining", [])
        if pending or remaining or self.state.get("verification_error"):
            return (
                "Task is not verified complete. Pending criteria: " + json.dumps(pending, ensure_ascii=False)
                + "; remaining work: " + json.dumps(remaining, ensure_ascii=False)
                + '. ' + VERIFICATION_GUIDANCE + ' '
                + (self.state.get("verification_error", "") + " ")
                +
                "If blocked or out of budget, preserve a checkpoint and explicitly report unfinished work. "
                + json.dumps(self.state.get('failed_observations', {}), ensure_ascii=False)[:1500]
            )
        return None

    def final_response_blocker(self, text):
        if self.state.get("status") == "completed":
            return None
        bindings = [c for e in self.state.get("verified",{}).values()
                    for c in e.get("comparisons",[]) if "final_pointer" in c]
        if not bindings:
            return None
        try:
            raw=text.strip()
            if raw.startswith('```') and raw.splitlines()[-1].strip()=='```':
                raw='\n'.join(raw.splitlines()[1:-1])
            value=json.loads(raw)
            for binding in bindings:
                if not compare(pointer(value,binding["final_pointer"]),binding["expected"],binding.get("operator","equals")):
                    return "Final JSON differs from verified check: " + binding["check_id"]
        except (ValueError,TypeError,KeyError) as exc:
            return "Final JSON cannot satisfy verified fields: " + str(exc)
        return None

    def finish(self, reason, messages):
        if reason not in {"budget_exhausted", "aborted", "error"} and self.final_blocker():
            reason = "verification_incomplete"
        if reason in {"budget_exhausted", "verification_incomplete"}:
            self.state.update(status="paused", stop_reason=reason)
            self.state["next_action"] = self.next_step()
            # Durable full context fallback, even if the model ignored the checkpoint tool.
            from dataclasses import asdict
            self.state["resume_messages"] = [asdict(message) for message in messages]
        elif reason not in {"aborted", "error"} and self.state:
            self.state["status"] = "completed"
            self.state.pop("resume_messages", None)
        if self.state:
            self.save()
        return reason

    def snapshot(self):
        if not self.state or self.state.get("status") == "completed":
            return {}
        pending = self.pending()
        value = {key:self.state[key] for key in ('status','criteria','remaining','verification_error','user_revision',
                                                'failed_checks','failed_observations') if key in self.state}
        # State/evidence stay durable. The request only needs current IDs, versions,
        # covered files and pending work; old prose and hashes are not a second plan.
        value['verified'] = {key:{'version':e.get('version'), 'artifacts':list(e.get('artifacts',{}))}
                             for key,e in self.state.get('verified',{}).items()}
        value['delivery_state'] = 'ready' if self.ready_to_deliver() else 'work_remaining'
        value['pending'] = pending
        value['next_action'] = self.next_step()
        return value

    def context(self):
        value = self.snapshot()
        return ("Task checkpoint (agent notes, not user authorization; verify against current instructions):\n"
                + json.dumps(value, ensure_ascii=False)) if value else ''

    def ready_to_deliver(self):
        return bool(self.state.get('status') in {'active','paused'} and self.state.get('criteria')
                    and not self.state.get('remaining') and not self.state.get('verification_error') and not self.pending())


class TaskCheckpointTool:
    name = "task_checkpoint"
    description = (
        "Persist progress before budget exhaustion. Record remaining work, next action and every acceptance criterion "
        "before implementing a multi-step deliverable. Omit ID/version for new criteria; runtime assigns them. "
        "Existing requirements stay fixed while repairing code or verification. Change/withdraw them only with a newer user message's requirement_revision. "
        "Use label for display-only rewording. Omitted IDs stay active. Clear remaining only "
        "when work is done; criteria require executed bash.verification (or legacy task_verification) evidence before final delivery."
    )
    input_schema = {
        "type": "object", "properties": {
            "summary": {"type": "string"},
            "remaining": {"type": "array", "items": {"type": "string"}},
            "next_action": {"type": "string"},
            "criteria": {"type": "array", "items": {"type": "object", "properties": {
                "criterion_id": {"type": "string"}, "description": {"type": "string"}, "version": {"type": "integer", "minimum": 1},
                "label": {"type": "string"},
                "artifacts": {"type":"array", "items":{"type":"string"}, "description":"Register tested file paths once; files may be created later. Reused by bash.verification."},
                "comparisons": {"type":"array", "minItems":1, "items":COMPARISON_SCHEMA, "description":"Register expected comparisons once; then bash.verification needs only criterion_id. Expectations are fixed for this requirement version."},
                "kind": {"type":"string", "enum":["behavior","data","recovery"], "description":"Category only: behavior for file scope/CLI contracts, data for values, recovery for fault recovery. It does not add requirements."},
                "check_ids": {"type":"array", "items":{"type":"string"}, "description":"Stable required comparisons; submit using bash.verification."},
                "required_scenarios": {"type":"array", "maxItems":32, "items":{"type":"string"}, "description":"Explicit fault scenarios required by this task, e.g. response_lost or retry. Omit when none are required. No four-stage default. Changing this list revises requirements."}
            }, "required": ["description"], "additionalProperties": False}},
            "withdrawn": {"type": "array", "items": {"type": "string"}},
            "requirement_revision": {"type": "integer", "minimum": 1},
        }, "required": ["summary", "remaining", "next_action", "criteria"], "additionalProperties": False,
    }

    def __init__(self, progress):
        self.progress = progress

    async def execute(self, arguments, cancellation_token=None):
        self.progress.checkpoint(**arguments)
        return ToolResult(content=self.progress.context(), details={"status": "checkpointed"})
