from __future__ import annotations

import unittest

from MiniClaw.benchmark.toolsafe import parse_prediction, select_cases


class ToolSafeBenchmarkTests(unittest.TestCase):
    def test_parser_accepts_json_and_rejects_out_of_range_rating(self) -> None:
        rating, payload, error = parse_prediction(
            '{"malicious_user_request": false, "being_attacked": false, '
            '"harmfulness_rating": 0.5, "reason": "risk later"}'
        )
        self.assertEqual(rating, 0.5)
        self.assertIsNotNone(payload)
        self.assertIsNone(error)

        rating, _, error = parse_prediction('{"harmfulness_rating": 0.75}')
        self.assertIsNone(rating)
        self.assertIn("must be", error or "")

    def test_selection_is_stratified_and_uses_unique_interactions(self) -> None:
        rows = []
        for score in (0.0, 0.5, 1.0):
            for index in range(4):
                rows.append(
                    {
                        "id-interaction": f"{score}-{index}",
                        "id-segment": 0,
                        "score": score,
                        "instruction": "request",
                        "history": "history",
                        "current_action": "action",
                        "env_info": "tools",
                    }
                )
        selected = select_cases(rows, seed=7, targets=((0.0, 2), (0.5, 2), (1.0, 2)))
        self.assertEqual(len(selected), 6)
        self.assertEqual(len({item.interaction_id for item in selected}), 6)
        self.assertEqual(
            {label: sum(item.expected == label for item in selected) for label in (0.0, 0.5, 1.0)},
            {0.0: 2, 0.5: 2, 1.0: 2},
        )


if __name__ == "__main__":
    unittest.main()
