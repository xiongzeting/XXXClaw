from __future__ import annotations

import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from MiniClaw.evaluation.models import EvalCheck, load_eval_suite
from MiniClaw.evaluation.runner import (
    _aggregate_metrics,
    _evaluate_budgets,
    _regression_alerts,
    _summarize,
    evaluate_check,
    run_eval_suite,
)


TEST_TEMP_ROOT = Path.cwd() / "tests" / ".eval-tmp"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


@contextmanager
def temporary_directory():
    path = TEST_TEMP_ROOT / uuid.uuid4().hex
    path.mkdir(parents=True)
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


class EvaluationModelTests(unittest.TestCase):
    def test_loads_version_one_suite(self) -> None:
        with temporary_directory() as directory:
            path = Path(directory) / "suite.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "name": "unit",
                        "cases": [
                            {
                                "id": "case-a",
                                "category": "coding",
                                "prompt": "do the task",
                                "checks": [{"type": "file_exists", "path": "done.txt"}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            suite = load_eval_suite(path)

        self.assertEqual(suite.name, "unit")
        self.assertEqual(suite.cases[0].phases[0].id, "main")

    def test_rejects_duplicate_case_ids(self) -> None:
        with temporary_directory() as directory:
            path = Path(directory) / "suite.json"
            case = {
                "id": "same",
                "category": "coding",
                "prompt": "task",
                "checks": [{"type": "file_exists", "path": "done.txt"}],
            }
            path.write_text(
                json.dumps({"version": 1, "name": "unit", "cases": [case, case]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unique"):
                load_eval_suite(path)

    def test_loads_reliability_settings_and_rejects_unknown_budget(self) -> None:
        with temporary_directory() as directory:
            path = Path(directory) / "suite.json"
            case = {
                "id": "repeatable",
                "category": "coding",
                "prompt": "task",
                "repetitions": 3,
                "min_pass_rate": 0.66,
                "checks": [{"type": "final_equals", "text": "done"}],
                "budgets": {"max_ttft_p95_ms": 5000},
            }
            path.write_text(
                json.dumps({"version": 1, "name": "unit", "cases": [case]}),
                encoding="utf-8",
            )
            suite = load_eval_suite(path)
            self.assertEqual(suite.cases[0].repetitions, 3)
            self.assertEqual(suite.cases[0].min_pass_rate, 0.66)
            case["budgets"] = {"made_up_budget": 1}
            path.write_text(
                json.dumps({"version": 1, "name": "unit", "cases": [case]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unknown budget"):
                load_eval_suite(path)

    def test_composes_suites_and_loads_phase_controls(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            child = root / "child.json"
            child.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "name": "child",
                        "environment": {"MINICLAW_SANDBOX": "host"},
                        "cases": [
                            {
                                "id": "retry",
                                "category": "reliability",
                                "capabilities": ["model_retry"],
                                "session_mode": "shared",
                                "phases": [
                                    {
                                        "id": "main",
                                        "prompt": "recover",
                                        "control": {"inject_http_statuses": [429]},
                                    }
                                ],
                                "checks": [{"type": "final_equals", "text": "done"}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            parent = root / "parent.json"
            parent.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "name": "parent",
                        "includes": ["child.json"],
                        "coverage": {
                            "dimensions": ["outcome", "reliability"],
                            "capabilities": ["model_retry"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            suite = load_eval_suite(parent)

        self.assertEqual(len(suite.cases), 1)
        self.assertEqual(suite.cases[0].session_mode, "shared")
        self.assertEqual(suite.cases[0].phases[0].control["inject_http_statuses"], [429])
        self.assertEqual(suite.cases[0].environment["MINICLAW_SANDBOX"], "host")
        self.assertEqual(suite.required_capabilities, ("model_retry",))


class EvaluationCheckTests(unittest.TestCase):
    def test_file_command_and_trace_checks(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "value.txt").write_text("ready\n", encoding="utf-8")
            phases = {"main": {"final_text": "all ready", "goal": {"status": "complete"}}}
            traces = {
                "main": [
                    {
                        "type": "tool.call",
                        "data": {"tool_name": "bash", "status": "success", "count": 2},
                    }
                ]
            }
            checks = [
                EvalCheck("file_contains", options={"path": "value.txt", "text": "ready"}),
                EvalCheck(
                    "command",
                    options={
                        "command": ["python", "-c", "raise SystemExit(0)"],
                        "exit_code": 0,
                    },
                ),
                EvalCheck(
                    "trace_event",
                    options={
                        "phase": "main",
                        "event": "tool.call",
                        "where": {"data.tool_name": "bash"},
                        "where_min": {"data.count": 2},
                    },
                ),
                EvalCheck(
                    "trace_event",
                    options={
                        "phase": "main",
                        "event": "tool.call",
                        "where_in": {"data.tool_name": ["read", "bash"]},
                    },
                ),
            ]
            results = [evaluate_check(check, workspace, root, phases, traces) for check in checks]

        self.assertTrue(all(result["passed"] for result in results))

    def test_path_check_cannot_escape_workspace(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            result = evaluate_check(
                EvalCheck("file_exists", options={"path": "../outside.txt"}),
                workspace,
                root,
                {},
                {},
            )

        self.assertFalse(result["passed"])
        self.assertIn("ValueError", result["detail"])

    def test_workspace_diff_and_trace_sequence(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            traces = {
                "main": [
                    {"type": "run.started", "timestamp": "1", "data": {}},
                    {
                        "type": "tool.call",
                        "timestamp": "2",
                        "data": {"tool_name": "edit", "status": "success"},
                    },
                    {"type": "run.completed", "timestamp": "3", "data": {}},
                ]
            }
            sequence = evaluate_check(
                EvalCheck(
                    "trace_sequence",
                    dimension="process",
                    options={
                        "sequence": [
                            {"event": "run.started"},
                            {"event": "tool.call", "where": {"data.tool_name": "edit"}},
                            {"event": "run.completed"},
                        ]
                    },
                ),
                workspace,
                root,
                {},
                traces,
            )
            diff = evaluate_check(
                EvalCheck(
                    "workspace_diff",
                    dimension="safety",
                    options={"required_paths": ["src/*.py"], "allowed_paths": ["src/*.py"]},
                ),
                workspace,
                root,
                {},
                traces,
                workspace_changes={"created": [], "modified": ["src/app.py"], "deleted": []},
            )

        self.assertTrue(sequence["passed"])
        self.assertTrue(diff["passed"])


class EvaluationMetricTests(unittest.TestCase):
    def test_aggregates_run_metrics_and_budgets(self) -> None:
        metrics = _aggregate_metrics(
            {
                "main": [
                    {
                        "type": "run.completed",
                        "data": {
                            "duration_ms": 1250,
                            "usage": {
                                "input_tokens": 100,
                                "output_tokens": 20,
                                "cached_tokens": 10,
                                "total_tokens": 120,
                                "cost_usd": 0.01,
                            },
                            "process": {
                                "model_requests": 2,
                                "tool_calls": 3,
                                "tool_errors": 1,
                                "compactions": 1,
                                "tokens_saved_by_compaction": 40,
                            },
                        },
                    }
                ]
            }
        )
        budgets = _evaluate_budgets(
            {"max_total_tokens": 120, "max_duration_seconds": 2}, metrics
        )

        self.assertEqual(metrics["tool_calls"], 3)
        self.assertEqual(metrics["tokens_saved_by_compaction"], 40)
        self.assertTrue(all(item["passed"] for item in budgets))

    def test_separates_agent_and_auxiliary_cost_and_counts_compaction_layers(self) -> None:
        metrics = _aggregate_metrics(
            {
                "main": [
                    {
                        "type": "model.request",
                        "data": {
                            "purpose": "agent",
                            "status": "success",
                            "usage": {"total_tokens": 100},
                        },
                    },
                    {
                        "type": "model.request",
                        "data": {
                            "purpose": "memory_consolidation",
                            "status": "success",
                            "usage": {"total_tokens": 40},
                        },
                    },
                    {
                        "type": "tool.call",
                        "data": {
                            "tool_name": "bash",
                            "status": "success",
                            "details": {"context_artifact": {"byte_size": 12000}},
                        },
                    },
                    {
                        "type": "compaction.completed",
                        "data": {
                            "strategy": "model-summary",
                            "tokens_saved": 500,
                            "details": {
                                "archive": {"path": "archive.jsonl"},
                                "tool_artifacts": [{"path": "tool.txt"}],
                            },
                        },
                    },
                ]
            }
        )
        budgets = _evaluate_budgets(
            {
                "max_agent_model_requests": 1,
                "max_auxiliary_model_requests": 1,
                "min_live_tool_artifacts": 1,
                "min_archived_tool_artifacts": 1,
                "min_history_archives": 1,
                "min_model_summary_compactions": 1,
            },
            metrics,
        )

        self.assertEqual(metrics["agent_tokens"], 100)
        self.assertEqual(metrics["memory_tokens"], 40)
        self.assertEqual(metrics["live_tool_artifact_bytes"], 12000)
        self.assertTrue(all(item["passed"] for item in budgets))

    def test_compaction_deferral_is_not_cancellation_or_failure(self) -> None:
        metrics = _aggregate_metrics({"main": [
            {"type": "compaction.started", "data": {}},
            {"type": "compaction.deferred", "data": {"reason": "soft_requires_model"}},
            {"type": "compaction.started", "data": {}},
            {"type": "compaction.aborted", "data": {"status": "cancelled"}},
            {"type": "compaction.started", "data": {}},
            {"type": "compaction.failed", "data": {"error": "test fault"}},
        ]})
        self.assertEqual(metrics['compaction_attempts'], 3)
        self.assertEqual(metrics['compaction_deferred'], 1)
        self.assertEqual(metrics['compaction_aborted'], 1)
        self.assertEqual(metrics['compaction_failures'], 1)

    def test_summary_and_regression_alerts(self) -> None:
        with temporary_directory() as directory:
            suite_path = Path(directory) / "suite.json"
            suite_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "name": "unit",
                        "cases": [
                            {
                                "id": "case-a",
                                "category": "coding",
                                "prompt": "task",
                                "checks": [{"type": "file_exists", "path": "done.txt"}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            suite = load_eval_suite(suite_path)
        result = {
            "passed": True,
            "category": "coding",
            "id": "case-a",
            "repetitions": 1,
            "passed_attempts": 1,
            "pass_rate": 1.0,
            "stable": True,
            "duration_seconds": 1.0,
            "failure_reasons": [],
            "coverage": {"tools": ["read"], "dimensions": ["outcome"]},
            "dimensions": {
                "outcome": {"score": 1.0},
                "process": {"score": 0.5},
                "efficiency": {"score": 1.0},
                "safety": {"score": None},
                "reliability": {"score": None},
            },
            "metrics": {"total_tokens": 100, "cost_usd": 0.01, "model_requests": 1},
        }
        summary = _summarize(suite, [result], elapsed_seconds=1.0)
        alerts = _regression_alerts(
            {**summary, "pass_rate": 0.8, "metrics": {"total_tokens": 120}},
            {**summary, "pass_rate": 1.0, "metrics": {"total_tokens": 100}},
        )

        self.assertEqual(summary["pass_rate"], 1.0)
        self.assertTrue(any("pass rate" in alert for alert in alerts))
        self.assertTrue(any("token" in alert.lower() for alert in alerts))


class EvaluationRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_refuses_non_empty_output_directory(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            suite_path = root / "suite.json"
            suite_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "name": "unit",
                        "cases": [
                            {
                                "id": "case-a",
                                "category": "coding",
                                "prompt": "task",
                                "checks": [{"type": "file_exists", "path": "done.txt"}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            suite = load_eval_suite(suite_path)
            output = root / "output"
            output.mkdir()
            (output / "existing.txt").write_text("keep", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                await run_eval_suite(suite, output_directory=output, environment={})


if __name__ == "__main__":
    unittest.main()
