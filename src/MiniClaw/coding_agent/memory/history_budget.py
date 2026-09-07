"""Project closed tool pairs under a cumulative byte budget; keep raw audit intact."""
from __future__ import annotations

import json
import hashlib
import os
from dataclasses import asdict
from MiniClaw.llm.types import ChatMessage, ToolInvocation

from .artifacts import ARTIFACT_MARKER


def bound_tool_history(messages, originals, completed, fresh, store, budget, *, high_water=None):
    calls = {c.call_id: c for m in messages for c in m.tool_calls}
    results = {m.tool_call_id: m for m in messages if m.role == 'tool'}
    raw_calls = {c.call_id: c for m in originals for c in m.tool_calls}
    raw_results = {m.tool_call_id: m for m in originals if m.role == 'tool'}
    ordered = [m.tool_call_id for m in messages if m.role == 'tool' and m.tool_call_id in completed]
    protected = fresh | set(ordered[-2:])

    def size(identifier):
        return len(json.dumps(calls[identifier].arguments, ensure_ascii=False).encode('utf-8')) + len(results[identifier].content.encode('utf-8'))

    before = total = sum(size(i) for i in ordered)
    rebase = total > (budget if high_water is None else high_water)
    archived = 0
    for identifier in ordered:
        if not rebase or total <= budget:
            break
        if identifier in protected:
            continue
        call, result = calls[identifier], results[identifier]
        payload = json.dumps({'call': asdict(raw_calls[identifier]), 'result': asdict(raw_results[identifier])}, ensure_ascii=False)
        artifact = store(identifier, 'closed-tool-pair', payload, 'json')
        reference = f'{ARTIFACT_MARKER} Historical {call.name}; full call/result: {artifact.path}'

        def shorten(value):
            if isinstance(value, str):
                return reference if ARTIFACT_MARKER not in value and len(reference.encode('utf-8')) < len(value.encode('utf-8')) else value
            if isinstance(value, dict):
                return {k: shorten(v) for k, v in value.items()}
            if isinstance(value, list):
                return [shorten(v) for v in value]
            return value

        old_size = size(identifier)
        call.arguments = shorten(call.arguments)
        result.content = shorten(result.content)
        saved = old_size - size(identifier)
        total -= saved
        archived += int(saved > 0)
    return {'closed_tool_bytes_before': before, 'closed_tool_bytes_after': total,
            'closed_tool_budget_bytes': budget, 'closed_tool_pairs_archived': archived,
            'closed_tool_budget_excess_bytes': max(0, total - budget),
            'history_rebase': rebase, 'closed_tool_high_water_bytes': high_water or budget}


class StableRequestView:
    """Persist the projection already sent; shrink it only at explicit boundaries.

    This file is a disposable view, never the source transcript or current file
    evidence. Raw message hashes prevent reuse across changed/compacted history.
    """
    def __init__(self, path, settings):
        self.path = path
        self.settings = settings
        self.signatures = []
        self.messages = []
        self.stage = []
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if value.get('settings') == settings:
                self.signatures = value['signatures']
                self.messages = value['messages']
                self.stage = value['stage']
                if len(self.signatures) != len(self.messages):
                    self.signatures = []; self.messages = []
        except (OSError, ValueError, KeyError, TypeError):
            self.signatures = []; self.messages = []

    @staticmethod
    def keys(messages):
        return [hashlib.sha256(json.dumps(asdict(m), sort_keys=True, ensure_ascii=False).encode()).hexdigest() for m in messages]

    @staticmethod
    def user_stage(messages, signatures):
        return [s for m, s in zip(messages, signatures)
                if m.role == 'user' and not m.content.startswith('[COMPLETION_CHECK]')]

    def prefix(self, raw):
        signatures = self.keys(raw)
        n = len(self.signatures)
        if (n and signatures[:n] == self.signatures and self.user_stage(raw, signatures) == self.stage):
            try:
                view = [ChatMessage(**{**m, 'tool_calls': [ToolInvocation(**c) for c in m.get('tool_calls', [])]}) for m in self.messages]
                return view, n
            except (TypeError, KeyError):
                pass
        return [], 0

    def save(self, raw, view):
        self.signatures = self.keys(raw)
        self.stage = self.user_stage(raw, self.signatures)
        self.messages = [asdict(m) for m in view]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'settings': self.settings, 'signatures': self.signatures,
                                        'stage': self.stage, 'messages': self.messages}, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, self.path)
