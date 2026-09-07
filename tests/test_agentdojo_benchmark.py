from __future__ import annotations

import unittest

from MiniClaw.benchmark.agentdojo import _score_summary


class AgentDojoBenchmarkTests(unittest.TestCase):
    def test_attack_security_field_is_reported_as_attack_success(self) -> None:
        results = {
            "utility_results": {("u0", "i0"): True, ("u1", "i0"): False},
            "security_results": {("u0", "i0"): True, ("u1", "i0"): False},
            "injection_tasks_utility_results": {"i0": True},
        }

        summary = _score_summary(results, attacked=True)

        self.assertEqual(summary["attack_successes"], 1)
        self.assertEqual(summary["attack_total"], 2)
        self.assertEqual(summary["attack_success_rate"], 0.5)
        self.assertEqual(summary["safe_count"], 1)
        self.assertEqual(summary["safe_rate"], 0.5)

    def test_non_attack_run_does_not_claim_a_security_rate(self) -> None:
        results = {
            "utility_results": {("u0", ""): True},
            "security_results": {("u0", ""): True},
            "injection_tasks_utility_results": {},
        }

        summary = _score_summary(results, attacked=False)

        self.assertIsNone(summary["attack_success_rate"])
        self.assertIsNone(summary["safe_rate"])
        self.assertEqual(summary["attack_total"], 0)


if __name__ == "__main__":
    unittest.main()
