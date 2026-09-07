from __future__ import annotations

import unittest
from pathlib import Path

from MiniClaw.benchmark.agentsafety import (
    compute_metrics,
    load_cases,
    load_official_system_prompt,
    parse_prediction,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = PROJECT_ROOT / "external" / "benchmarks" / "AgentSafety"


class AgentSafetyBenchmarkTests(unittest.TestCase):
    def test_upstream_prompt_and_all_cases_load(self) -> None:
        prompt = load_official_system_prompt(BENCHMARK_ROOT)
        cases = load_cases(BENCHMARK_ROOT)

        self.assertIn("ALLOW", prompt)
        self.assertIn("ASK", prompt)
        self.assertIn("REFUSE", prompt)
        self.assertEqual(len(cases), 50)
        self.assertEqual({case["expected_label"] for case in cases}, {"allow", "ask", "refuse"})

    def test_prediction_parser_accepts_json_and_rejects_unknown_label(self) -> None:
        label, payload, error = parse_prediction('{"reasoning":"bounded","label":"ALLOW"}')
        self.assertEqual(label, "allow")
        self.assertIsNotNone(payload)
        self.assertIsNone(error)

        label, _, error = parse_prediction('{"reasoning":"unsafe","label":"deny"}')
        self.assertIsNone(label)
        self.assertIn("allow, ask, or refuse", error or "")

    def test_metrics_distinguish_over_and_under_caution(self) -> None:
        results = [
            {
                "case": {"expected_label": "allow", "domain": "files"},
                "prediction": "ask",
                "passed": False,
                "usage": {"input_tokens": 10, "output_tokens": 2, "cached_tokens": 0},
                "duration_ms": 100,
            },
            {
                "case": {"expected_label": "refuse", "domain": "bash"},
                "prediction": "ask",
                "passed": False,
                "usage": {"input_tokens": 12, "output_tokens": 3, "cached_tokens": 1},
                "duration_ms": 200,
            },
            {
                "case": {"expected_label": "ask", "domain": "network"},
                "prediction": "ask",
                "passed": True,
                "usage": {"input_tokens": 11, "output_tokens": 2, "cached_tokens": 0},
                "duration_ms": 300,
            },
        ]

        metrics = compute_metrics(results)

        self.assertEqual(metrics["passed"], 1)
        self.assertEqual(metrics["over_cautious"], 1)
        self.assertEqual(metrics["under_cautious"], 1)
        self.assertEqual(metrics["usage"]["input_tokens"], 33)


if __name__ == "__main__":
    unittest.main()
