"""External benchmark adapters for MiniClaw."""

from importlib import import_module
from typing import Any

__all__ = [
    "BenchmarkCase",
    "FactSearchTool",
    "benchmark_memory_config",
    "parse_fact_documents",
    "score_answer",
]


def __getattr__(name: str) -> Any:
    """Expose adapter helpers without pre-importing executable modules.

    Eagerly importing ``memory_agent_bench`` here causes ``python -m`` to find
    the target module already loaded and emits a runpy warning.  Lazy exports
    preserve the package API while ensuring the CLI module is initialized once.
    """

    if name not in __all__:
        raise AttributeError(name)
    module = import_module(".memory_agent_bench", __name__)
    return getattr(module, name)
