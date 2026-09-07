from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from .types import ModelEvent, ModelRequest


class ModelClient(Protocol):
    """Provider-neutral model client used by the agent loop."""

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...
