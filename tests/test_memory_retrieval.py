from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from MiniClaw.coding_agent.memory.retrieval import (
    HybridMemoryRetriever,
    LocalHashVectorEncoder,
    MemoryDocument,
    RetrievalConfig,
    SentenceTransformerVectorEncoder,
    create_hybrid_memory_retriever,
    exact_search_symbols,
)
from MiniClaw.coding_agent.memory.semantic import MemoryConflictError, SemanticMemoryStore


class HybridRetrievalTests(unittest.TestCase):
    class RecordingCrossEncoder:
        model_name = "fake-cross-encoder"
        load_error = ""

        def __init__(self, *, unavailable: bool = False) -> None:
            self.unavailable = unavailable
            self.calls: list[list[str]] = []

        def score(self, query, documents):
            self.calls.append([document.record_id for document in documents])
            if self.unavailable:
                self.load_error = "model cache missing"
                return None
            return [8.0 if document.record_id == "relevant" else -8.0 for document in documents]

    @staticmethod
    def _cross_encoder_documents() -> list[MemoryDocument]:
        return [
            MemoryDocument("distractor", "archive", "target phrase repeated target phrase"),
            MemoryDocument("relevant", "archive", "target phrase with the true evidence"),
            MemoryDocument("other", "archive", "target phrase but unrelated filler"),
        ]

    def test_cross_encoder_reranks_only_the_bounded_candidate_pool(self) -> None:
        reranker = self.RecordingCrossEncoder()
        retriever = HybridMemoryRetriever(
            RetrievalConfig(
                candidate_pool=3,
                cross_encoder_candidate_pool=2,
                cross_encoder_weight=0.9,
            ),
            reranker=reranker,
        )

        hits = retriever.search("target phrase", self._cross_encoder_documents(), 3)

        self.assertEqual(hits[0].document.record_id, "relevant")
        self.assertEqual(len(reranker.calls), 1)
        self.assertEqual(len(reranker.calls[0]), 2)
        self.assertTrue(retriever.last_diagnostics["cross_encoder_applied"])
        self.assertEqual(retriever.last_diagnostics["cross_encoder_candidates"], 2)

    def test_cross_encoder_can_be_disabled_for_an_internal_recall_stage(self) -> None:
        reranker = self.RecordingCrossEncoder()
        retriever = HybridMemoryRetriever(reranker=reranker)

        hits = retriever.search(
            "target phrase",
            self._cross_encoder_documents(),
            3,
            use_cross_encoder=False,
        )

        self.assertEqual(reranker.calls, [])
        self.assertTrue(all(hit.cross_encoder_score is None for hit in hits))
        self.assertFalse(retriever.last_diagnostics["cross_encoder_applied"])

    def test_cross_encoder_load_failure_falls_back_to_deterministic_order(self) -> None:
        baseline = HybridMemoryRetriever().search(
            "target phrase",
            self._cross_encoder_documents(),
            3,
        )
        reranker = self.RecordingCrossEncoder(unavailable=True)
        retriever = HybridMemoryRetriever(reranker=reranker)

        fallback = retriever.search("target phrase", self._cross_encoder_documents(), 3)

        self.assertEqual(
            [hit.document.record_id for hit in fallback],
            [hit.document.record_id for hit in baseline],
        )
        self.assertFalse(retriever.last_diagnostics["cross_encoder_applied"])
        self.assertEqual(
            retriever.last_diagnostics["cross_encoder_load_error"],
            "model cache missing",
        )

    def test_environment_factory_is_opt_in_and_validates_limits(self) -> None:
        disabled = create_hybrid_memory_retriever({})
        enabled = create_hybrid_memory_retriever(
            {"MINICLAW_MEMORY_CROSS_ENCODER_ENABLED": "true"}
        )

        self.assertIsNone(disabled.reranker)
        self.assertIsNotNone(enabled.reranker)
        with self.assertRaisesRegex(ValueError, "must be between 1 and 50"):
            create_hybrid_memory_retriever(
                {
                    "MINICLAW_MEMORY_CROSS_ENCODER_ENABLED": "true",
                    "MINICLAW_MEMORY_CROSS_ENCODER_CANDIDATES": "51",
                }
            )
        with self.assertRaisesRegex(ValueError, "must be a number"):
            create_hybrid_memory_retriever(
                {
                    "MINICLAW_MEMORY_CROSS_ENCODER_ENABLED": "true",
                    "MINICLAW_MEMORY_CROSS_ENCODER_WEIGHT": "heavy",
                }
            )

    def test_environment_factory_enables_real_local_bi_encoder(self) -> None:
        retriever = create_hybrid_memory_retriever(
            {
                "MINICLAW_MEMORY_VECTOR_ENABLED": "true",
                "MINICLAW_MEMORY_VECTOR_MODEL": "sentence-transformers/test-model",
                "MINICLAW_MEMORY_VECTOR_BATCH_SIZE": "17",
            }
        )
        self.assertIsInstance(retriever.vector_encoder, SentenceTransformerVectorEncoder)
        self.assertEqual(retriever.vector_encoder.model_name, "sentence-transformers/test-model")
        self.assertEqual(retriever.vector_encoder.batch_size, 17)

        disabled = create_hybrid_memory_retriever(
            {"MINICLAW_MEMORY_VECTOR_ENABLED": "false"}
        )
        self.assertIsInstance(disabled.vector_encoder, LocalHashVectorEncoder)

    def test_bi_encoder_batches_and_caches_local_model_vectors(self) -> None:
        class FakeModel:
            def __init__(self) -> None:
                self.calls = []

            def encode(self, texts, **kwargs):
                self.calls.append((list(texts), kwargs))
                return [[1.0, 0.0] for _ in texts]

        model = FakeModel()
        encoder = SentenceTransformerVectorEncoder("fake", batch_size=7)
        encoder._model = lambda: model  # type: ignore[method-assign]

        first = encoder.encode_many(["alpha", "beta", "alpha"])
        second = encoder.encode_many(["alpha", "beta"])

        self.assertEqual(len(first), 3)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(model.calls[0][0], ["alpha", "beta"])
        self.assertEqual(model.calls[0][1]["batch_size"], 7)
        self.assertEqual(second, first[:2])
        self.assertEqual(encoder.last_backend, "sentence-transformer-cache")

    def test_persistent_cache_reuses_document_and_query_vectors_after_restart(self) -> None:
        class CountingEncoder:
            dimensions = 2

            def __init__(self) -> None:
                self.calls = 0

            def encode_many(self, texts):
                self.calls += 1
                return [[1.0, 0.0] for _ in texts]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "retrieval.sqlite3"
            documents = [MemoryDocument("one", "fact", "persistent target evidence")]
            first_encoder = CountingEncoder()
            first = HybridMemoryRetriever(
                vector_encoder=first_encoder,
                cache_path=path,
            )
            first.search("persistent target", documents, 1)

            second_encoder = CountingEncoder()
            restarted = HybridMemoryRetriever(
                vector_encoder=second_encoder,
                cache_path=path,
            )
            hits = restarted.search("persistent target", documents, 1)

            self.assertEqual(hits[0].document.record_id, "one")
            self.assertEqual(first_encoder.calls, 1)
            self.assertEqual(second_encoder.calls, 0)
            self.assertEqual(restarted.last_diagnostics["ann_backend"], "faiss-hnsw")
            self.assertTrue(restarted.last_diagnostics["ann_applied"])

    def test_persistent_ann_filters_results_to_current_scope(self) -> None:
        class ScopeEncoder:
            dimensions = 2

            @staticmethod
            def encode_many(texts):
                values = []
                for text in texts:
                    values.append([1.0, 0.0] if "shared" in text else [0.0, 1.0])
                return values

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "retrieval.sqlite3"
            retriever = HybridMemoryRetriever(
                vector_encoder=ScopeEncoder(),
                cache_path=cache_path,
            )
            foreign = MemoryDocument("foreign", "fact", "shared secret from another user")
            allowed = MemoryDocument("allowed", "fact", "shared local material")
            retriever.search("shared", [foreign], 1)
            hits = retriever.search("shared", [allowed], 1)

            self.assertEqual([hit.document.record_id for hit in hits], ["allowed"])
            self.assertEqual(retriever.last_diagnostics["ann_scope_size"], 1)
            self.assertEqual(retriever.last_diagnostics["ann_index_size"], 2)

    def test_steady_state_ann_query_encodes_only_the_new_query(self) -> None:
        class RecordingEncoder:
            dimensions = 2

            def __init__(self) -> None:
                self.batches: list[list[str]] = []

            def encode_many(self, texts):
                self.batches.append(list(texts))
                return [[1.0, 0.0] for _ in texts]

        with tempfile.TemporaryDirectory() as directory:
            encoder = RecordingEncoder()
            retriever = HybridMemoryRetriever(
                vector_encoder=encoder,
                cache_path=Path(directory) / "retrieval.sqlite3",
            )
            documents = [
                MemoryDocument(str(index), "fact", f"persistent evidence {index}")
                for index in range(6)
            ]
            retriever.search("first query", documents, 3)
            retriever.search("second unseen query", documents, 3)

            self.assertEqual(len(encoder.batches[0]), len(documents) + 1)
            self.assertEqual(encoder.batches[1], ["second unseen query"])
            self.assertEqual(
                retriever.last_diagnostics["ann_vector_source"],
                "persistent-index",
            )

    def test_inactive_ann_document_is_reactivated_when_it_reenters_scope(self) -> None:
        class Encoder:
            dimensions = 2

            @staticmethod
            def encode_many(texts):
                return [[1.0, 0.0] for _ in texts]

        with tempfile.TemporaryDirectory() as directory:
            retriever = HybridMemoryRetriever(
                vector_encoder=Encoder(),
                cache_path=Path(directory) / "retrieval.sqlite3",
            )
            document = MemoryDocument("returning", "fact", "returning ANN evidence")
            retriever.search("returning", [document], 1)
            assert retriever.cache is not None
            document_key = retriever.cache.document_key(document)
            with retriever.cache._connection() as connection:
                connection.execute(
                    "UPDATE retrieval_ann_items SET active = 0 WHERE document_key = ?",
                    (document_key,),
                )

            hits = retriever.search("returning again", [document], 1)

            with retriever.cache._connection() as connection:
                active = connection.execute(
                    "SELECT active FROM retrieval_ann_items WHERE document_key = ?",
                    (document_key,),
                ).fetchone()
            self.assertEqual([hit.document.record_id for hit in hits], ["returning"])
            self.assertEqual(active, (1,))
            self.assertEqual(retriever.last_diagnostics["ann_backend"], "faiss-hnsw")

    def test_ann_dimension_change_uses_a_separate_persistent_index(self) -> None:
        class Encoder:
            def __init__(self, dimensions):
                self.dimensions = dimensions

            def encode_many(self, texts):
                return [[1.0, *([0.0] * (self.dimensions - 1))] for _ in texts]

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "retrieval.sqlite3"
            document = MemoryDocument("one", "fact", "dimension target")
            first = HybridMemoryRetriever(vector_encoder=Encoder(2), cache_path=cache_path)
            second = HybridMemoryRetriever(vector_encoder=Encoder(3), cache_path=cache_path)

            first.search("dimension target", [document], 1)
            hits = second.search("dimension target", [document], 1)

            self.assertEqual(hits[0].document.record_id, "one")
            ann_files = list(cache_path.with_suffix(".sqlite3.ann").glob("*.faiss"))
            self.assertEqual(len(ann_files), 2)

    def test_missing_faiss_falls_back_to_linear_cosine(self) -> None:
        class Encoder:
            dimensions = 2

            @staticmethod
            def encode_many(texts):
                return [[1.0, 0.0] for _ in texts]

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "sys.modules", {"faiss": None}
        ):
            retriever = HybridMemoryRetriever(
                vector_encoder=Encoder(),
                cache_path=Path(directory) / "retrieval.sqlite3",
            )
            hits = retriever.search(
                "target",
                [MemoryDocument("one", "fact", "target evidence")],
                1,
            )

            self.assertEqual(hits[0].document.record_id, "one")
            self.assertEqual(retriever.last_diagnostics["ann_backend"], "linear-fallback")
            self.assertFalse(retriever.last_diagnostics["ann_applied"])

    def test_missing_bi_encoder_cache_falls_back_without_network(self) -> None:
        encoder = SentenceTransformerVectorEncoder(
            "sentence-transformers/definitely-not-cached-miniclaw-test"
        )
        retriever = HybridMemoryRetriever(vector_encoder=encoder)
        hits = retriever.search(
            "semantic target",
            [MemoryDocument("a", "archive", "semantic target evidence")],
            1,
        )
        self.assertEqual(hits[0].document.record_id, "a")
        self.assertEqual(encoder.last_backend, "local-hash-fallback")
        self.assertTrue(encoder.load_error)
        self.assertTrue(retriever.last_diagnostics["vector_fallback"])
        self.assertEqual(
            retriever.last_diagnostics["vector_backend"],
            "local-hash-fallback",
        )

    def test_bi_encoder_environment_rejects_invalid_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            create_hybrid_memory_retriever(
                {
                    "MINICLAW_MEMORY_VECTOR_ENABLED": "true",
                    "MINICLAW_MEMORY_VECTOR_MODEL": " ",
                }
            )
        with self.assertRaisesRegex(ValueError, "must be between 1 and 512"):
            create_hybrid_memory_retriever(
                {
                    "MINICLAW_MEMORY_VECTOR_ENABLED": "true",
                    "MINICLAW_MEMORY_VECTOR_BATCH_SIZE": "0",
                }
            )

    def test_exact_multiword_topic_beats_isolated_token_collision(self) -> None:
        class MisleadingVectorEncoder:
            @staticmethod
            def encode_many(texts):
                vectors = [[1.0, 0.0]]
                for text in texts[1:]:
                    if "distributed systems workshop experiences" in text:
                        vectors.append([0.0, 1.0])
                    else:
                        vectors.append([1.0, 0.0])
                return vectors

        documents = [
            MemoryDocument(
                f"distractor-{index}",
                "archive",
                f"Do you think this is a good idea? I am still on a high after concert "
                f"number {index} and want distributed recommendations.",
            )
            for index in range(24)
        ]
        documents.append(
            MemoryDocument(
                "evidence",
                "archive",
                "I remember useful distributed systems workshop experiences from the lab.",
            )
        )

        hits = HybridMemoryRetriever(vector_encoder=MisleadingVectorEncoder()).search(
            "Should I attend the distributed systems workshop?",
            documents,
            5,
        )

        self.assertEqual(hits[0].document.record_id, "evidence")

    def test_exact_search_prioritizes_paths_error_codes_and_code_symbols(self) -> None:
        query = "Fix ERR_CONNECTION_RESET in src/net/client.py inside HttpClient.send_request"
        symbols = exact_search_symbols(query)
        self.assertIn("err_connection_reset", symbols)
        self.assertIn("src/net/client.py", symbols)
        self.assertIn("httpclient.send_request", symbols)
        documents = [
            MemoryDocument(
                "broad",
                "archive",
                "The network client has a connection problem in another module.",
            ),
            MemoryDocument(
                "exact",
                "archive",
                "src/net/client.py raised ERR_CONNECTION_RESET in HttpClient.send_request.",
            ),
        ]

        hits = HybridMemoryRetriever().search(query, documents, 2)

        self.assertEqual(hits[0].document.record_id, "exact")
        self.assertEqual(hits[0].exact_rank, 1)
        self.assertGreater(hits[0].exact_score, 0.0)

    def test_exact_search_does_not_override_irrelevant_natural_language(self) -> None:
        documents = [
            MemoryDocument("relevant", "fact", "deployment failed because the cache was full"),
            MemoryDocument("symbol", "fact", "A generic note mentioning CacheManager only"),
        ]
        hits = HybridMemoryRetriever().search("Why did the deployment fail?", documents, 2)
        self.assertEqual(hits[0].document.record_id, "relevant")

    def test_vector_encoder_can_batch_documents(self) -> None:
        class BatchEncoder:
            def __init__(self) -> None:
                self.calls = 0

            def encode_many(self, texts: list[str]) -> list[list[float]]:
                self.calls += 1
                return [[1.0, 0.0] if "target" in text else [0.0, 1.0] for text in texts]

        encoder = BatchEncoder()
        retriever = HybridMemoryRetriever(vector_encoder=encoder)
        hits = retriever.search(
            "target",
            [
                MemoryDocument("a", "fact", "unrelated"),
                MemoryDocument("b", "fact", "target evidence"),
            ],
            2,
        )
        self.assertEqual(encoder.calls, 1)
        self.assertEqual(hits[0].document.record_id, "b")

    def test_repeated_query_terms_do_not_overpower_decisive_rare_terms(self) -> None:
        documents = [
            MemoryDocument(
                record_id="panel",
                category="archive",
                content="The gateway showed an amber badge beside the deployment panel.",
            ),
            MemoryDocument(
                record_id="timeout",
                category="archive",
                content="The gateway stalled and retried after repeated upstream timeout errors.",
            ),
        ]
        query = (
            "The gateway appeared on the deployment panel. Gateway gateway gateway gateway "
            "then stalled with repeated upstream timeout errors."
        )
        hits = HybridMemoryRetriever().search(query, documents, 2)
        self.assertEqual(hits[0].document.record_id, "timeout")

    def test_bm25_is_primary_and_vector_is_auxiliary(self) -> None:
        config = RetrievalConfig()
        self.assertGreater(config.bm25_weight, config.vector_weight)
        documents = [
            MemoryDocument("db", "project", "数据库使用 PostgreSQL，连接池为 asyncpg", "数据库"),
            MemoryDocument("style", "preference", "用户偏好简洁的中文回答", "回答风格"),
            MemoryDocument("cache", "project", "缓存使用 Redis，TTL 为 10 分钟", "缓存"),
        ]
        hits = HybridMemoryRetriever(config).search("PostgreSQL 数据库连接", documents, 3)
        self.assertEqual(hits[0].document.record_id, "db")
        self.assertEqual(hits[0].bm25_rank, 1)
        self.assertEqual(hits[0].vector_rank, 1)
        self.assertGreater(hits[0].rrf_score, 0)

    def test_vector_recall_can_rescue_a_non_bm25_candidate_before_rerank(self) -> None:
        documents = [
            MemoryDocument("exact", "project", "database engine is PostgreSQL"),
            MemoryDocument("aux", "project", "postgres backup process uses pg_dump"),
            MemoryDocument("other", "fact", "frontend uses React"),
        ]
        hits = HybridMemoryRetriever().search("postgresql database", documents, 3)
        auxiliary = next(hit for hit in hits if hit.document.record_id == "aux")
        self.assertIsNone(auxiliary.bm25_rank)
        self.assertIsNotNone(auxiliary.vector_rank)
        self.assertGreater(auxiliary.vector_score, 0)

    def test_versioned_conflicts_are_resolved_before_relevance_ranking(self) -> None:
        documents = [
            MemoryDocument(
                "deployment_4",
                "fact",
                "service deployment uses image app:v1.",
                subject="service deployment",
                relation="uses image",
                value="app:v1",
                version=4,
            ),
            MemoryDocument(
                "deployment_9",
                "fact",
                "service deployment uses image app:v2.",
                subject="service deployment",
                relation="uses image",
                value="app:v2",
                version=9,
            ),
            MemoryDocument("other", "fact", "worker deployment uses image jobs:v3"),
        ]
        hits = HybridMemoryRetriever().search("service deployment image", documents, 5)
        ids = [hit.document.record_id for hit in hits]
        self.assertIn("deployment_9", ids)
        self.assertNotIn("deployment_4", ids)
        latest = next(hit for hit in hits if hit.document.record_id == "deployment_9")
        self.assertEqual(latest.superseded_record_ids, ("deployment_4",))

    def test_unstructured_documents_are_not_collapsed(self) -> None:
        documents = [
            MemoryDocument("a", "fact", "project runtime is Docker", subject="runtime"),
            MemoryDocument("b", "fact", "project runtime is host", subject="runtime"),
        ]
        hits = HybridMemoryRetriever().search("project runtime", documents, 5)
        self.assertEqual({hit.document.record_id for hit in hits}, {"a", "b"})

    def test_completed_memory_beats_equally_relevant_failed_memory(self) -> None:
        documents = [
            MemoryDocument(
                "failed",
                "episode",
                "parser incident root cause was cache corruption",
                status="failed",
            ),
            MemoryDocument(
                "completed",
                "episode",
                "parser incident root cause was cache corruption",
                status="completed",
            ),
        ]

        hits = HybridMemoryRetriever().search("parser incident root cause", documents, 2)

        self.assertEqual(hits[0].document.record_id, "completed")

    def test_latest_query_prefers_newer_equally_relevant_memory(self) -> None:
        documents = [
            MemoryDocument(
                "old",
                "episode",
                "deployment runtime is Docker",
                created_at="2020-01-01T00:00:00+00:00",
                status="completed",
            ),
            MemoryDocument(
                "new",
                "episode",
                "deployment runtime is Docker",
                created_at="2099-01-01T00:00:00+00:00",
                status="completed",
            ),
        ]

        hits = HybridMemoryRetriever().search("latest deployment runtime", documents, 2)

        self.assertEqual(hits[0].document.record_id, "new")

    def test_prompt_uses_hybrid_ranked_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SemanticMemoryStore(Path(directory) / "MEMORY.md")
            store.remember("project", "数据库使用 PostgreSQL")
            store.remember("project", "数据库缓存使用 Redis")
            prompt = store.render_for_prompt("PostgreSQL 数据库")
            self.assertIn("BM25-primary", prompt)
            self.assertLess(prompt.index("PostgreSQL"), prompt.index("Redis"))


class MemoryConflictTests(unittest.TestCase):
    def test_conflict_is_persisted_and_can_be_resolved_by_replace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "MEMORY.md"
            store = SemanticMemoryStore(path)
            store.remember("project", "项目数据库使用 PostgreSQL")
            with self.assertRaises(MemoryConflictError) as raised:
                store.remember("project", "项目数据库使用 MySQL")
            conflict = raised.exception.conflict
            self.assertEqual(conflict.status, "pending")
            self.assertEqual(store.list_conflicts()[0].conflict_id, conflict.conflict_id)

            restarted = SemanticMemoryStore(path)
            restarted.resolve_conflict(conflict.conflict_id, "replace")
            self.assertIn("项目数据库使用 MySQL", restarted.read())
            self.assertNotIn("项目数据库使用 PostgreSQL", restarted.read())
            resolved = restarted.list_conflicts("resolved")[0]
            self.assertEqual(resolved.resolution, "replace")

    def test_conflict_policies_support_keep_existing_and_keep_both(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SemanticMemoryStore(Path(directory) / "MEMORY.md")
            store.remember("environment", "Python 版本: 3.11")
            result = store.remember(
                "environment",
                "Python 版本: 3.12",
                conflict_policy="keep_existing",
            )
            self.assertIn("Kept existing", result)
            store.remember(
                "environment",
                "Python 版本: 3.10",
                conflict_policy="keep_both",
            )
            self.assertIn("3.11", store.read())
            self.assertIn("3.10", store.read())

    def test_file_lock_prevents_lost_updates_from_concurrent_writers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "MEMORY.md"
            store_a = SemanticMemoryStore(path)
            store_b = SemanticMemoryStore(path)
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(store_a.remember, "fact", "构建系统: setuptools"),
                    executor.submit(store_b.remember, "fact", "测试系统: unittest"),
                ]
                for future in futures:
                    future.result()
            text = SemanticMemoryStore(path).read()
            self.assertIn("构建系统: setuptools", text)
            self.assertIn("测试系统: unittest", text)


if __name__ == "__main__":
    unittest.main()
