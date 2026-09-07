from __future__ import annotations

import unittest

from MiniClaw.coding_agent.memory.query_tracker import QueryTracker


class QueryTrackerTests(unittest.TestCase):
    def test_related_query_with_new_evidence_is_not_blocked(self) -> None:
        tracker = QueryTracker(similarity_threshold=0.7)
        tracker.observe("archive:s1", "Alice project status", 5, {"a"}, [{"recordId": "a"}])
        progress = tracker.observe(
            "archive:s1",
            "Alice project current status",
            5,
            {"a", "b"},
            [{"recordId": "a"}, {"recordId": "b"}],
        )
        self.assertEqual(progress.status, "new_evidence")
        self.assertEqual(progress.new_evidence_ids, ("b",))

    def test_tracker_scope_and_instance_are_isolated(self) -> None:
        first = QueryTracker()
        second = QueryTracker()
        first.observe("archive:s1", "same query", 5, {"a"}, [{"recordId": "a"}])
        self.assertIsNone(first.cached("archive:s2", "same query", 5))
        self.assertIsNone(second.cached("archive:s1", "same query", 5))


if __name__ == "__main__":
    unittest.main()
