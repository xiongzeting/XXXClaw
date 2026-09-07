from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from MiniClaw.benchmark.ai_efficiency import selected_tasks
from MiniClaw.evaluation.splits import (
    LongMemQuestion,
    _stratified_pick,
    track_commands,
    validate_split_manifest,
)


class EvalSplitTests(unittest.TestCase):
    def test_ai_efficiency_selection_respects_frozen_id_order(self) -> None:
        manifest = {
            "log_small_easy": {"task_id": "log_small_easy"},
            "log_ci_medium": {"task_id": "log_ci_medium"},
        }

        selected = selected_tasks(manifest, ["log_ci_medium", "log_small_easy"])

        self.assertEqual(
            [item["task_id"] for item in selected],
            ["log_ci_medium", "log_small_easy"],
        )

    def test_stratified_pick_is_deterministic_and_balanced(self) -> None:
        candidates = [
            LongMemQuestion(
                question_id=f"{domain}-{question_type}-{index}",
                domain=domain,
                question_type=question_type,
                component=f"component-{domain}-{question_type}-{index}",
            )
            for domain in ("web", "enterprise")
            for question_type in ("procedure", "static-environment")
            for index in range(4)
        ]

        first = _stratified_pick(candidates, 8, seed=17)
        second = _stratified_pick(candidates, 8, seed=17)

        self.assertEqual(first, second)
        self.assertEqual(len({item.question_id for item in first}), 8)
        self.assertEqual(len({item.stratum for item in first}), 4)

    def test_manifest_validation_rejects_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection = root / "selection.ids"
            selection.write_text("case-a\n", encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "tracks": {"heldout": {"selection_file": "selection.ids"}},
                        "counts": {"heldout_cases": 1},
                        "leakage_checks": {"development_heldout_question_overlap": 1},
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "leakage"):
                validate_split_manifest(manifest, project_root=root)

    def test_current_frozen_manifest_is_valid(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = validate_split_manifest(
            root / "evals" / "splits" / "v1" / "manifest.json",
            project_root=root,
        )

        self.assertTrue(result["valid"])
        self.assertEqual(result["exposure_registries"], 1)
        self.assertEqual(result["counts"]["total_declared_executions"], 274)
        self.assertEqual(
            len(track_commands(root / "evals" / "splits" / "v1" / "manifest.json", "development")),
            3,
        )


if __name__ == "__main__":
    unittest.main()
