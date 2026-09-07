"""Immutable, session-local command captures for binding-only revalidation."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path


class VerificationEvidenceStore:
    MAX_BYTES = 1_048_576

    def __init__(self, session_directory):
        self.root = Path(session_directory).resolve() / 'verification-evidence'

    def _check_root(self):
        if self.root.resolve() != self.root:
            raise ValueError('Evidence directory redirected; refusing access')

    def save(self, payload):
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
        if len(raw) > self.MAX_BYTES:
            raise ValueError('Verification evidence exceeds 1 MiB; use a smaller observation record')
        self._check_root()
        self.root.mkdir(parents=True, exist_ok=True)
        identifier = uuid.uuid4().hex
        path = self.root / (identifier + '.json')
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(raw)
        os.replace(temporary, path)
        return identifier, hashlib.sha256(raw).hexdigest()

    def load(self, identifier, digest):
        self._check_root()
        if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
            raise ValueError('Invalid evidence_id; use an ID returned by this session')
        path = self.root / (identifier + '.json')
        if path.is_symlink() or path.stat().st_size > self.MAX_BYTES:
            raise ValueError('Invalid evidence capture')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('Evidence capture changed; refusing revalidation')
        return json.loads(raw)
