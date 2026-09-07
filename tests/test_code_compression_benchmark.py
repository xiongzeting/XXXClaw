from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from MiniClaw.benchmark.code_compression import (
    Instance,
    arm_environment,
    build_task_prompt,
    capture_patch,
    prepare_arm_workspace,
    trace_metrics,
)
from MiniClaw.coding_agent.memory.config import load_memory_config
from MiniClaw.llm.types import ModelProfile
from MiniClaw.benchmark.swebench_runner import write_text_lf


class CodeCompressionBenchmarkTests(unittest.TestCase):
    def test_arms_change_only_the_named_compaction_policy(self) -> None:
        profile = ModelProfile("fake", context_window=128_000, max_output_tokens=8_192)
        legacy = load_memory_config(profile, arm_environment({}, "legacy-summary-recent"))
        layered = load_memory_config(profile, arm_environment({}, "layered-current"))

        self.assertEqual(legacy.strategy, "legacy-summary-recent")
        self.assertEqual(layered.strategy, "layered-current")
        self.assertFalse(legacy.progressive_enabled)
        self.assertTrue(layered.progressive_enabled)
        self.assertEqual(legacy.hard_trigger_tokens, layered.hard_trigger_tokens)
        self.assertEqual(legacy.keep_recent_tokens, layered.keep_recent_tokens)

    def test_prompt_is_instance_stable_and_arm_independent(self) -> None:
        prompt = build_task_prompt(Instance("repo__repo-1", "Fix the parser."))
        self.assertIn("repo__repo-1", prompt)
        self.assertIn("Fix the parser.", prompt)
        self.assertNotIn("layered", prompt)
        self.assertNotIn("legacy", prompt)

    def test_workspace_clone_and_patch_capture_exclude_internal_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            clone = root / "clone"
            source.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
            (source / "tracked.txt").write_text("before\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=source, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=source, check=True)

            prepare_arm_workspace(source, clone)
            self.assertEqual(
                subprocess.run(
                    ["git", "rev-list", "--count", "HEAD"],
                    cwd=clone,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                "1",
            )
            (clone / "tracked.txt").write_text("after\n", encoding="utf-8")
            (clone / "new.txt").write_text("new\n", encoding="utf-8")
            (clone / ".aster").mkdir()
            (clone / ".aster" / "session.jsonl").write_text("internal", encoding="utf-8")
            patch = capture_patch(clone)

            self.assertIn("tracked.txt", patch)
            self.assertIn("new.txt", patch)
            self.assertNotIn("session.jsonl", patch)

    def test_patch_capture_recovers_from_stale_index_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            clone = root / "clone"
            source.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
            (source / "tracked.txt").write_text("before\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=source, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=source, check=True)

            prepare_arm_workspace(source, clone)
            (clone / "tracked.txt").write_text("after\n", encoding="utf-8")
            (clone / ".git" / "index.lock").write_bytes(b"")
            patch = capture_patch(clone)

            self.assertIn("+after", patch)
            self.assertFalse((clone / ".git" / "index.lock").exists())

    def test_swebench_windows_wrapper_writes_linux_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "patch.diff"
            write_text_lf(path, "first\nsecond\n", encoding="utf-8")
            self.assertEqual(path.read_bytes(), b"first\nsecond\n")

    @unittest.skipUnless(os.name == "nt", "Windows read-only file semantics")
    def test_workspace_preparation_removes_readonly_git_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            clone = root / "clone"
            source.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
            (source / "tracked.txt").write_text("value\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=source, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=source, check=True)
            packed = source / ".git" / "objects" / "readonly.pack"
            packed.write_bytes(b"pack")
            packed.chmod(stat.S_IREAD)

            prepare_arm_workspace(source, clone)

            self.assertEqual(
                subprocess.run(
                    ["git", "rev-list", "--count", "HEAD"],
                    cwd=clone,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                "1",
            )

    def test_trace_metrics_report_each_compaction_layer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            records = [
                {
                    "schema_version": 1,
                    "trace_id": "t",
                    "event_id": "1",
                    "type": "model.request",
                    "data": {
                        "purpose": "agent",
                        "status": "success",
                        "duration_ms": 10,
                        "usage": {"input_tokens": 120, "output_tokens": 5, "total_tokens": 125},
                    },
                },
                {
                    "schema_version": 1,
                    "trace_id": "t",
                    "event_id": "2",
                    "type": "tool.call",
                    "data": {
                        "status": "success",
                        "details": {"context_artifact": {"byte_size": 200}},
                    },
                },
                {
                    "schema_version": 1,
                    "trace_id": "t",
                    "event_id": "3",
                    "type": "compaction.completed",
                    "data": {
                        "tokens_saved": 90,
                        "details": {
                            "layers_applied": [
                                "old-result-reference",
                                "closed-history-archive",
                                "model-summary",
                            ],
                            "tool_artifacts": [{}, {}],
                            "archive_indexed_chunks": 4,
                            "archive": {"byte_size": 500},
                        },
                    },
                },
            ]
            path.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")

            metrics = trace_metrics(path)

            self.assertEqual(metrics["peak_agent_input_tokens"], 120)
            self.assertEqual(metrics["l1_live_artifacts"], 1)
            self.assertEqual(metrics["l2_old_tool_results_archived"], 2)
            self.assertEqual(metrics["l2_closed_history_archives"], 1)
            self.assertEqual(metrics["archive_indexed_chunks"], 4)
            self.assertEqual(metrics["tokens_saved_by_compaction"], 90)


if __name__ == "__main__":
    unittest.main()
