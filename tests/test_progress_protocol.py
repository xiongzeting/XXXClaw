"""Completion regressions observed in the real frontend challenge runs."""
import json
import tempfile
import unittest
from pathlib import Path

from MiniClaw.coding_agent.assistant.progress import TaskProgress
from MiniClaw.coding_agent.tools.base import ToolResult
from MiniClaw.llm.types import ToolInvocation
from tests import test_task_recovery as recovery_helpers


class VersionedProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "app.py").write_text("value = 1", encoding="utf-8")
        self.progress = TaskProgress(self.root / ".session", self.root)
        self.progress.checkpoint("implement", [], "verify", [self.criterion()])

    def criterion(self, description="valid behavior", version=1):
        return {"criterion_id": "behavior", "description": description, "version": version}

    def verify(self, version=1, **extra):
        payload = {"task_checks": [{"criterion_id": "behavior", "version": version,
            "passed": True, "evidence": "actual output equals expected value", "artifacts": ["app.py"], **extra}]}
        self.progress.observe(ToolInvocation("check", "bash", {"task_verification": True}),
            ToolResult(json.dumps(payload) + "\nwarning: optional plugin unavailable",
                details={"stdout": json.dumps(payload), "stderr": "warning: optional plugin unavailable", "exit_code": 0,
                         "workspace_changes": {"complete": True, "changed_paths": []}}))

    def test_reword_keeps_id_and_evidence_but_version_change_requires_new_check(self):
        self.verify()
        self.progress.checkpoint("ready", [], "deliver", [{**self.criterion(), 'label': 'same check, clearer wording'}])
        self.assertIsNone(self.progress.final_blocker())
        self.assertEqual(len(self.progress.state["criteria"]), 1)
        self.progress.begin_user_turn('Change the required behavior')
        self.progress.checkpoint("changed requirement", [], "verify", [self.criterion("new behavior", 2)], requirement_revision=1)
        self.assertIsNotNone(self.progress.final_blocker())
        self.verify(version=1)
        self.assertIsNotNone(self.progress.final_blocker())
        self.verify(version=2)
        self.assertIsNone(self.progress.final_blocker())

    def test_stderr_diagnostics_do_not_break_valid_stdout(self):
        self.verify()
        self.assertIsNone(self.progress.final_blocker())

    def test_withdrawal_is_explicit_and_audited(self):
        self.progress.checkpoint("same", [], "verify", [])
        self.assertEqual(self.progress.pending(), ["valid behavior"])
        self.progress.begin_user_turn('Withdraw the behavior requirement')
        self.progress.checkpoint("user withdrew", [], "deliver", [], withdrawn=["behavior"], requirement_revision=1)
        self.assertEqual(self.progress.pending(), [])
        self.assertEqual(self.progress.state["criterion_changes"][-1]["action"], "withdraw")

    def test_readonly_retains_and_changed_dependency_invalidates(self):
        self.verify()
        self.progress.observe(ToolInvocation("inspect", "bash", {}), ToolResult("OK", details={
            "workspace_changes": {"complete": True, "changed_paths": []}}))
        self.assertIsNone(self.progress.final_blocker())
        (self.root / "app.py").write_text("value = 2", encoding="utf-8")
        self.assertIsNotNone(self.progress.final_blocker())

    def test_unrelated_file_retains_scoped_evidence_unknown_shell_invalidates(self):
        self.verify()
        self.progress.observe(ToolInvocation("write", "write", {"path": "notes.txt"}), ToolResult("written"))
        self.assertIsNone(self.progress.final_blocker())
        self.progress.observe(ToolInvocation("unknown", "bash", {}), ToolResult("unknown effects"))
        self.assertIsNotNone(self.progress.final_blocker())

    def test_malformed_verification_and_outside_artifact_do_not_complete(self):
        for extra in ({"passed": "true"}, {"evidence": ""}, {"artifacts": ["../outside.py"]}):
            self.verify(**extra)
            self.assertIsNotNone(self.progress.final_blocker())

    def test_pause_next_action_is_pending_work_not_stale_done_claim(self):
        self.progress.checkpoint("done", [], "无，已完成", [])
        self.progress.finish("budget_exhausted", [])
        self.assertIn("valid behavior", self.progress.state["next_action"])
        self.assertNotIn("已完成", self.progress.state["next_action"])

    def test_legacy_session_load_preserves_verified_criteria(self):
        self.progress.path.write_text(json.dumps({"criteria": ["old"], "verified": {"old": {"evidence": "checked"}}}), encoding="utf-8")
        loaded = TaskProgress(self.progress.path.parent, self.root)
        self.assertEqual(loaded.pending(), [])
        self.assertEqual(loaded.state["criteria"][0]["criterion_id"], "old")


class GoalAvailabilityTests(unittest.TestCase):
    def test_goal_completion_only_available_with_explicit_goal(self):
        with tempfile.TemporaryDirectory() as directory:
            assistant = recovery_helpers.RecoveryTests().assistant(None, directory)
            self.assertNotIn("goal_complete", assistant.tool_executor.available_names())
            assistant.create_goal("deliver", ["verified"])
            assistant._build_system_prompt()
            self.assertIn("goal_complete", assistant.tool_executor.available_names())
