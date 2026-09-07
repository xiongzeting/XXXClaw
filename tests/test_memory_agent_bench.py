from __future__ import annotations

import asyncio
import json
import unittest

from MiniClaw.benchmark.memory_agent_bench import (
    FactSearchTool,
    benchmark_memory_config,
    parse_fact_documents,
    score_answer,
)
from MiniClaw.llm.types import ModelProfile


class MemoryAgentBenchAdapterTests(unittest.TestCase):
    def test_fact_parser_and_search_use_miniclaw_hybrid_retriever(self) -> None:
        documents = parse_fact_documents(
            "Here is a list of facts:\n"
            "0. The author of Our Mutual Friend is Charles Dickens.\n"
            "1. Charles Dickens is married to Catherine Dickens.\n"
            "2. Catherine Dickens is a citizen of Belgium.\n"
        )
        self.assertEqual(len(documents), 3)
        self.assertEqual(documents[0].subject, "The author of Our Mutual Friend")
        self.assertEqual(documents[0].relation, "is")
        self.assertEqual(documents[0].value, "Charles Dickens")
        self.assertEqual(documents[0].version, 0)
        result = asyncio.run(FactSearchTool(documents).execute({"query": "Our Mutual Friend"}))
        payload = json.loads(result.content)
        self.assertIn("Charles Dickens", payload[0]["content"])

    def test_benchmark_search_exposes_only_latest_conflicting_fact(self) -> None:
        documents = parse_fact_documents(
            "328. association football was created in the country of England.\n"
            "1628. association football was created in the country of Italy.\n"
        )
        result = asyncio.run(
            FactSearchTool(documents).execute({"query": "association football created country"})
        )
        payload = json.loads(result.content)
        self.assertEqual(payload[0]["record_id"], "fact_1628")
        self.assertIn("Italy", payload[0]["content"])
        self.assertEqual(payload[0]["supersedes"], ["fact_328"])

    def test_exact_repeated_query_reuses_cached_retrieval(self) -> None:
        documents = parse_fact_documents(
            "0. Alice is married to Bob.\n1. Bob works in the field of Biology.\n"
        )
        tool = FactSearchTool(documents)
        original_search = tool.retriever.search
        calls = 0

        def counted_search(*args: object, **kwargs: object):
            nonlocal calls
            calls += 1
            return original_search(*args, **kwargs)

        tool.retriever.search = counted_search  # type: ignore[method-assign]
        asyncio.run(tool.execute({"query": "Alice spouse", "limit": 5}))
        repeated = asyncio.run(tool.execute({"query": " Alice, spouse! ", "limit": 5}))
        payload = json.loads(repeated.content)
        self.assertEqual(calls, 1)
        self.assertEqual(repeated.details["query_dedup"]["status"], "cache_hit")
        self.assertTrue(payload[-1]["_searchMeta"]["noProgress"])

    def test_near_duplicate_marks_no_progress_only_when_evidence_does_not_change(self) -> None:
        documents = parse_fact_documents(
            "0. Alice is married to Bob.\n1. Bob works in the field of Biology.\n"
        )
        tool = FactSearchTool(documents, default_limit=1)
        asyncio.run(tool.execute({"query": "Who is Alice married to", "limit": 1}))
        repeated = asyncio.run(tool.execute({"query": "Alice is married to whom", "limit": 1}))
        changed = asyncio.run(tool.execute({"query": "Bob works in which field", "limit": 1}))
        self.assertEqual(repeated.details["query_dedup"]["status"], "no_progress")
        self.assertEqual(changed.details["query_dedup"]["status"], "new_query")

    def test_substring_scoring_matches_official_conflict_metric_style(self) -> None:
        scored = score_answer("FINAL: Belgium", ("Belgium",))
        self.assertTrue(scored["exact_match"])
        self.assertTrue(scored["substring_exact_match"])

    def test_scaled_compaction_profile_preserves_requested_ratios(self) -> None:
        config = benchmark_memory_config(
            ModelProfile(model_id="test", context_window=128_000, max_output_tokens=256),
            True,
        )
        self.assertEqual(config.soft_trigger_tokens, 8_000)
        self.assertEqual(config.hard_trigger_tokens, 10_000)
        self.assertEqual(config.target_tokens, 3_000)
        self.assertEqual(config.keep_recent_tokens, 2_000)
        self.assertEqual(config.reserve_tokens, 2_560)


if __name__ == "__main__":
    unittest.main()
