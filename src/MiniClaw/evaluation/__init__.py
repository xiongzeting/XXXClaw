"""Project-level evaluation and benchmark evidence for MiniClaw.

The runner is loaded lazily so runtime tracing can live below this package
without importing the coding assistant back through ``evaluation.__init__``.
"""

from importlib import import_module

from .models import EvalCase, EvalCheck, EvalPhase, EvalSuite, load_eval_suite

__all__ = [
    "EvalCase",
    "EvalCheck",
    "EvalPhase",
    "EvalSuite",
    "load_eval_suite",
    "run_eval_suite",
]


def __getattr__(name):
    if name == "run_eval_suite":
        return import_module(".runner", __name__).run_eval_suite
    raise AttributeError(name)
