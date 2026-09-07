from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from MiniClaw.llm.types import (
    AssistantReply,
    ChatMessage,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    ToolInvocation,
)
from MiniClaw.coding_agent.memory.archive import ArchiveMemoryIndex, StableFactIngestor
from MiniClaw.coding_agent.memory.config import MemoryConfig
from MiniClaw.coding_agent.memory.evidence import MemoryEvidenceStore
from MiniClaw.coding_agent.memory.episodic import EpisodicMemoryStore
from MiniClaw.coding_agent.memory.manager import (
    RETRIEVAL_TOKEN_BUDGET,
    MemoryManager,
    RetrievedMemoryItem,
    estimate_retrieval_tokens,
)
from MiniClaw.coding_agent.memory.retrieval import (
    HybridMemoryRetriever,
    MemoryDocument,
    RetrievalHit,
)
from MiniClaw.coding_agent.memory.semantic import SemanticMemoryStore
from MiniClaw.coding_agent.memory.tools import MemoryTool


class FakeModelClient:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield ModelEvent(type="completed", reply=AssistantReply(content="checkpoint"))


def config() -> MemoryConfig:
    return MemoryConfig(
        enabled=True,
        reserve_tokens=20,
        keep_recent_tokens=20,
        soft_trigger_tokens=100,
        hard_trigger_tokens=120,
        target_tokens=80,
        progressive_enabled=True,
        artifact_threshold_bytes=32,
        artifact_preview_chars=12,
        deterministic_semantic_tokens=1,
    )


class ArchiveMemoryTests(unittest.TestCase):
    class MarkerRetriever:
        @staticmethod
        def search(query, documents, limit):
            hits = []
            terms = [term.casefold() for term in query.split() if term]
            for rank, document in enumerate(documents, start=1):
                content = document.content.casefold()
                matches = sum(term in content for term in terms)
                if not matches:
                    continue
                score = float(matches) + (1.0 / (rank + 1))
                hits.append(
                    RetrievalHit(
                        document=document,
                        score=score,
                        rrf_score=score,
                        bm25_score=score,
                        vector_score=0.0,
                        bm25_rank=rank,
                        vector_rank=None,
                    )
                )
            return sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit]

    def test_records_cache_reuses_one_jsonl_parse_until_the_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = ArchiveMemoryIndex(root / "archive.jsonl", root)
            archive.add_messages(
                "session",
                "session.jsonl",
                [ChatMessage(role="user", content="cached archive evidence")],
            )
            original_read_text = Path.read_text
            archive_reads = 0

            def counted_read_text(path: Path, *args, **kwargs):
                nonlocal archive_reads
                if path == archive.path:
                    archive_reads += 1
                return original_read_text(path, *args, **kwargs)

            with patch.object(Path, "read_text", new=counted_read_text):
                first = archive.records()
                scoped = archive.records("session")

            self.assertEqual(len(first), 1)
            self.assertEqual(scoped, first)
            self.assertEqual(archive_reads, 1)

    def test_evidence_cache_reuses_parse_and_invalidates_after_append(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryEvidenceStore(Path(directory) / "evidence.jsonl")
            store.append(kind="semantic", content="fact one", session_id="session-1")
            original_read_text = Path.read_text
            evidence_reads = 0

            def counted_read_text(path: Path, *args, **kwargs):
                nonlocal evidence_reads
                if path == store.path:
                    evidence_reads += 1
                return original_read_text(path, *args, **kwargs)

            with patch.object(Path, "read_text", new=counted_read_text):
                first = store.records()
                scoped = store.records(kinds={"semantic"})
                store.append(kind="episode", content="episode two", session_id="session-2")
                refreshed = store.records()

            self.assertEqual(scoped, first)
            self.assertEqual(len(refreshed), 2)
            self.assertEqual(evidence_reads, 2)

    def test_numbered_lines_are_archived_as_ordinary_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = ArchiveMemoryIndex(root / ".aster/memory/archive-index.jsonl", root)
            count = index.add_messages(
                "session",
                "archive.jsonl",
                [
                    ChatMessage(
                        role="user",
                        content="1. inspect the service\n2. update the configuration\n3. run tests",
                    )
                ],
            )
            self.assertEqual(count, 1)
            records = index.records("session")
            self.assertEqual(records[0].source_kind, "message")
            self.assertIn("2. update the configuration", records[0].content)

    def test_referenced_tool_artifact_is_indexed_by_full_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / ".aster/context-artifacts/session/tool.txt"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("rare diagnostic marker ZEBRA-417", encoding="utf-8")
            index = ArchiveMemoryIndex(
                root / ".aster/memory/archive-index.jsonl",
                root,
            )
            index.add_messages(
                "session",
                ".aster/context-artifacts/session/transcript.jsonl",
                [
                    ChatMessage(
                        role="tool",
                        content=(
                            "[MiniClaw context artifact]\n"
                            "Tool: bash\n"
                            "Full result: .aster/context-artifacts/session/tool.txt"
                        ),
                    )
                ],
            )
            hits = index.search("ZEBRA-417", 5, session_id="session")
            self.assertTrue(any("rare diagnostic marker" in hit.record.content for hit in hits))

    def test_angle_bracket_metadata_is_not_treated_as_a_special_session_format(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = ArchiveMemoryIndex(
                root / ".aster/memory/archive-index.jsonl",
                root,
                chunk_chars=400,
            )
            marker = '<event source="import" sequence="7">'
            index.add_messages(
                "session",
                "archive.jsonl",
                [ChatMessage(role="user", content=f"{marker}\n" + "ordinary evidence " * 120)],
            )
            records = index.records("session")
            self.assertGreater(len(records), 1)
            self.assertTrue(records[0].content.startswith(marker))
            self.assertTrue(all(not record.content.startswith(marker) for record in records[1:]))

    def test_persisted_archive_skips_derived_memory_tool_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = ArchiveMemoryIndex(root / ".aster/memory/archive-index.jsonl", root)
            index.add_messages(
                "session",
                "archive.jsonl",
                [
                    ChatMessage(role="tool", name="memory", content='[{"content":"derived"}]'),
                    ChatMessage(role="user", content="source evidence remains searchable"),
                ],
            )
            records = index.records("session")
            self.assertEqual([record.content for record in records], ["source evidence remains searchable"])

    def test_recency_query_expansion_is_generic(self) -> None:
        class QueryAwareRetriever:
            @staticmethod
            def search(query, documents, limit):
                expanded = "updated changed switched" in query
                hits = []
                for rank, document in enumerate(documents, start=1):
                    if expanded and "switched to the new queue" in document.content:
                        score = 0.90
                    elif not expanded and "current queue is the legacy queue" in document.content:
                        score = 0.80
                    else:
                        continue
                    hits.append(
                        RetrievalHit(
                            document=document,
                            score=score,
                            rrf_score=score,
                            bm25_score=score,
                            vector_score=0.0,
                            bm25_rank=rank,
                            vector_rank=None,
                        )
                    )
                return hits[:limit]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = ArchiveMemoryIndex(
                root / ".aster/memory/archive-index.jsonl",
                root,
                retriever=QueryAwareRetriever(),
            )
            index.add_messages(
                "session",
                "archive.jsonl",
                [
                    ChatMessage(role="user", content="The current queue is the legacy queue."),
                    ChatMessage(role="user", content="We switched to the new queue yesterday."),
                ],
            )
            hits = index.search("Which queue is currently used?", 2, session_id="session")
            self.assertIn("switched to the new queue", hits[0].record.content)

    def test_generic_recommendation_query_recalls_user_interest_profile(self) -> None:
        class QueryAwareRetriever:
            @staticmethod
            def search(query, documents, limit):
                expanded = "user interests preferences" in query
                hits = []
                for rank, document in enumerate(documents, start=1):
                    if expanded and "medical image analysis" in document.content:
                        score = 0.90
                    elif not expanded and "recommend some references" in document.content:
                        score = 0.80
                    else:
                        continue
                    hits.append(
                        RetrievalHit(
                            document=document,
                            score=score,
                            rrf_score=score,
                            bm25_score=score,
                            vector_score=0.0,
                            bm25_rank=rank,
                            vector_rank=None,
                        )
                    )
                return hits[:limit]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = ArchiveMemoryIndex(
                root / ".aster/memory/archive-index.jsonl",
                root,
                retriever=QueryAwareRetriever(),
            )
            index.add_messages(
                "session",
                "archive.jsonl",
                [
                    ChatMessage(
                        role="assistant",
                        content="I can recommend some references.",
                    ),
                    ChatMessage(
                        role="user",
                        content=(
                            "My research focus and field expertise are deep learning for medical "
                            "image analysis, and I repeatedly read papers and articles about it."
                        ),
                    ),
                ],
            )

            hits = index.search(
                "Can you recommend some references for me?",
                2,
                session_id="session",
            )

            self.assertIn("medical image analysis", hits[0].record.content)

    def test_search_deduplicates_overlapping_compaction_archives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = ArchiveMemoryIndex(root / ".aster/memory/archive-index.jsonl", root)
            duplicate = "The service emitted diagnostic code ZX-41 during startup."
            for archive_number in range(4):
                index.add_messages(
                    "session",
                    f"compaction-{archive_number}.jsonl",
                    [ChatMessage(role="user", content=duplicate)],
                )
            relevant = "The service recovered after the cache directory was recreated."
            index.add_messages(
                "session",
                "compaction-relevant.jsonl",
                [ChatMessage(role="user", content=relevant)],
            )
            hits = index.search("service startup ZX-41 recovered cache directory", 5, session_id="session")
            contents = [hit.record.content for hit in hits]
            self.assertEqual(contents.count(duplicate), 1)
            self.assertIn(relevant, contents)

    def test_parent_search_expands_anchor_neighbors_in_chunk_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = ArchiveMemoryIndex(
                root / ".aster/memory/archive-index.jsonl",
                root,
                retriever=self.MarkerRetriever(),
                chunk_chars=400,
            )
            lines = [
                "before-state " + ("a" * 300),
                "unique-anchor " + ("b" * 300),
                "after-state " + ("c" * 300),
                "unrelated-tail " + ("d" * 300),
            ]
            index.add_messages(
                "session",
                "archive.jsonl",
                [ChatMessage(role="user", content="\n".join(lines))],
            )

            hits = index.search_parents("unique-anchor", session_id="session")

            self.assertEqual(len(hits), 1)
            self.assertEqual([record.chunk_index for record in hits[0].records], [0, 1, 2])
            self.assertNotIn("unrelated-tail", hits[0].content)

    def test_parent_search_does_not_expand_across_message_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = ArchiveMemoryIndex(
                root / ".aster/memory/archive-index.jsonl",
                root,
                retriever=self.MarkerRetriever(),
                chunk_chars=400,
            )
            index.add_messages(
                "session",
                "archive.jsonl",
                [
                    ChatMessage(role="user", content="target-state " + ("a" * 500)),
                    ChatMessage(role="assistant", content="different-parent-neighbor"),
                ],
            )

            hit = index.search_parents("target-state", session_id="session")[0]

            self.assertEqual({record.message_index for record in hit.records}, {0})
            self.assertNotIn("different-parent-neighbor", hit.content)

    def test_parent_search_deduplicates_child_hits_and_caps_at_five_parents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = ArchiveMemoryIndex(
                root / ".aster/memory/archive-index.jsonl",
                root,
                retriever=self.MarkerRetriever(),
                chunk_chars=400,
            )
            messages = [
                ChatMessage(
                    role="user",
                    content="\n".join(f"shared parent-zero-{part} " + (str(part) * 280) for part in range(3)),
                ),
                *[
                    ChatMessage(role="user", content=f"shared parent-{number}")
                    for number in range(1, 7)
                ],
            ]
            index.add_messages("session", "archive.jsonl", messages)

            hits = index.search_parents("shared", parent_limit=20, session_id="session")

            self.assertEqual(len(hits), 5)
            self.assertEqual(len({hit.parent_id for hit in hits}), 5)

    def test_parent_search_preserves_session_filter_after_reloading_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / ".aster/memory/archive-index.jsonl"
            index = ArchiveMemoryIndex(path, root, retriever=self.MarkerRetriever())
            index.add_messages("session-a", "a.jsonl", [ChatMessage(role="user", content="needle alpha")])
            index.add_messages("session-b", "b.jsonl", [ChatMessage(role="user", content="needle beta")])

            reloaded = ArchiveMemoryIndex(path, root, retriever=self.MarkerRetriever())
            hits = reloaded.search_parents("needle", session_id="session-a")

            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].anchor.session_id, "session-a")
            self.assertNotIn("beta", hits[0].content)

    def test_action_block_returns_tool_call_result_and_followup_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = ArchiveMemoryIndex(
                root / ".aster/memory/archive-index.jsonl",
                root,
                retriever=self.MarkerRetriever(),
            )
            index.add_messages(
                "session",
                "archive.jsonl",
                [
                    ChatMessage(
                        role="assistant",
                        tool_calls=[ToolInvocation("call-1", "bash", {"command": "pytest"})],
                    ),
                    ChatMessage(
                        role="tool",
                        name="bash",
                        tool_call_id="call-1",
                        content="ERROR ZX-99 in parser.py",
                    ),
                    ChatMessage(
                        role="assistant",
                        content="The parser fixture is stale; refresh it before rerunning pytest.",
                    ),
                ],
            )

            hit = index.search_parents("ZX-99", session_id="session")[0]

            self.assertTrue(hit.anchor.action_block_id)
            self.assertIn("Assistant tool calls", hit.content)
            self.assertIn("ERROR ZX-99", hit.content)
            self.assertIn("fixture is stale", hit.content)
            self.assertEqual(
                {record.event_kind for record in hit.records},
                {"tool_call", "tool_result", "decision"},
            )

    def test_old_archive_records_remain_readable_without_action_block_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / ".aster/memory/archive-index.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "record_id": "old",
                        "session_id": "session",
                        "source_kind": "message",
                        "source_path": "old.jsonl",
                        "role": "user",
                        "message_index": 0,
                        "chunk_index": 0,
                        "content": "legacy archive evidence",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            record = ArchiveMemoryIndex(path, root).records("session")[0]

            self.assertEqual(record.action_block_id, "")
            self.assertEqual(record.event_kind, "message")

    def test_stable_fact_ingestion_requires_explicit_user_directive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SemanticMemoryStore(Path(directory) / "MEMORY.md")
            ingestor = StableFactIngestor(store)
            stats = ingestor.ingest(
                [
                    ChatMessage(role="user", content="普通临时讨论：数据库可能换掉"),
                    ChatMessage(role="assistant", content="记住：[project] 不应采纳助手自述"),
                    ChatMessage(role="user", content="记住 [project]：项目数据库使用 PostgreSQL"),
                ]
            )
            self.assertEqual(stats["remembered"], 1)
            self.assertIn("PostgreSQL", store.read())
            self.assertNotIn("不应采纳", store.read())


class MemoryManagerRetrievalTests(unittest.TestCase):
    def test_tool_evidence_can_refresh_memory_during_the_same_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster/session.jsonl",
                session_id="session",
                model_client=FakeModelClient(),
                profile=ModelProfile("fake", context_window=1_000, max_output_tokens=20),
                config=config(),
                archive_retriever=ArchiveMemoryTests.MarkerRetriever(),
            )
            manager.semantic.remember("project", "ZX-99 means the parser fixture is stale")
            manager.prompt_context("repair failure")
            manager.append(
                ChatMessage(role="tool", name="bash", content="ERROR ZX-99 in parser.py")
            )

            refreshed, details = manager.maybe_refresh_prompt_context(manager.active_messages)

            self.assertIsNotNone(details)
            self.assertEqual(details["reason"], "tool_evidence")
            self.assertIn("ZX-99 means", refreshed)

    def test_dynamic_refresh_keeps_existing_high_score_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster/session.jsonl",
                session_id="session",
                model_client=FakeModelClient(),
                profile=ModelProfile("fake", context_window=1_000, max_output_tokens=20),
                config=config(),
            )
            existing = RetrievedMemoryItem(
                source="semantic",
                record_id="existing",
                content="existing high-value diagnosis",
                fused_score=0.99,
                metadata={},
            )
            refreshed = [
                RetrievedMemoryItem(
                    source="semantic",
                    record_id=f"novel-{index}",
                    content=f"novel evidence {index}",
                    fused_score=0.50 - index * 0.01,
                    metadata={},
                )
                for index in range(8)
            ]
            with patch.object(manager, "retrieve", return_value=[existing]):
                manager.prompt_context("repair failure")
            manager.append(
                ChatMessage(role="tool", name="bash", content="ERROR ZX-77 in parser.py")
            )

            with (
                patch.object(manager, "retrieve", return_value=refreshed),
                patch.object(manager, "_has_retrievable_memory", return_value=True),
            ):
                _, details = manager.maybe_refresh_prompt_context(manager.active_messages)

            self.assertIsNotNone(details)
            self.assertIn(
                ("semantic", "existing"),
                {(item.source, item.record_id) for item in manager.last_retrieval},
            )

    def test_episode_hit_automatically_follows_into_historical_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster/session.jsonl",
                session_id="current",
                model_client=FakeModelClient(),
                profile=ModelProfile("fake", context_window=1_000, max_output_tokens=20),
                config=config(),
            )
            manager.episodic.checkpoint(
                "old-session",
                [ChatMessage(role="user", content="Investigate parser marker ZX-41")],
                status="completed",
                summary="Parser incident ZX-41 was investigated",
            )
            manager.archive.add_messages(
                "old-session",
                "old.jsonl",
                [ChatMessage(role="tool", name="bash", content="ZX-41 root cause was cache corruption")],
            )

            items = manager.retrieve("What caused parser incident ZX-41?")

            archive = [item for item in items if item.source == "archive"]
            self.assertTrue(archive)
            self.assertIn("cache corruption", archive[0].content)
    def test_cross_encoder_reranks_archive_anchor_not_the_expanded_parent_prefix(self) -> None:
        class CapturingReranker:
            model_name = "capture"
            load_error = ""

            def __init__(self) -> None:
                self.documents: list[MemoryDocument] = []

            def score(self, query, documents):
                self.documents = list(documents)
                return [0.0] * len(documents)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reranker = CapturingReranker()
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster/session.jsonl",
                session_id="session",
                model_client=FakeModelClient(),
                profile=ModelProfile("fake", context_window=1_000, max_output_tokens=20),
                config=config(),
                archive_retriever=HybridMemoryRetriever(reranker=reranker),
            )
            manager.archive.chunk_chars = 400
            manager.archive.add_messages(
                "session",
                "archive.jsonl",
                [
                    ChatMessage(
                        role="user",
                        content="\n".join(
                            [
                                "prefix-neighbor " + ("a" * 300),
                                "unique-anchor " + ("b" * 300),
                                "suffix-neighbor " + ("c" * 300),
                            ]
                        ),
                    )
                ],
            )

            items = manager.retrieve("unique-anchor")

            archive_item = next(item for item in items if item.source == "archive")
            rerank_document = next(
                document
                for document in reranker.documents
                if document.record_id == archive_item.record_id
            )
            self.assertIn("unique-anchor", rerank_document.content)
            self.assertNotIn("prefix-neighbor", rerank_document.content)
            self.assertIn("prefix-neighbor", archive_item.content)

    def test_prompt_context_fuses_semantic_archive_and_episode_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster/session.jsonl",
                session_id="session",
                model_client=FakeModelClient(),
                profile=ModelProfile("fake", context_window=1_000, max_output_tokens=20),
                config=config(),
            )
            manager.semantic.remember("project", "数据库使用 PostgreSQL")
            manager.archive.add_messages(
                "session",
                "archive.jsonl",
                [ChatMessage(role="user", content="数据库迁移脚本位于 migrations/001.sql")],
            )
            manager.episodic.checkpoint(
                "old-session",
                [ChatMessage(role="user", content="检查数据库迁移")],
                summary="数据库迁移检查已完成",
            )
            prompt = manager.prompt_context("数据库迁移 PostgreSQL")
            self.assertIn("<retrieved_memory>", prompt)
            sources = {item.source for item in manager.last_retrieval}
            self.assertIn("semantic", sources)
            self.assertIn("archive", sources)
            self.assertIn("episode", sources)

    def test_automatic_retrieval_keeps_all_five_archive_parents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster/session.jsonl",
                session_id="session",
                model_client=FakeModelClient(),
                profile=ModelProfile("fake", context_window=1_000, max_output_tokens=20),
                config=config(),
                archive_retriever=ArchiveMemoryTests.MarkerRetriever(),
            )
            manager.archive.add_messages(
                "session",
                "archive.jsonl",
                [
                    ChatMessage(role="user", content=f"shared automatic-parent-{index}")
                    for index in range(7)
                ],
            )

            items = manager.retrieve("shared")

            self.assertEqual(sum(item.source == "archive" for item in items), 5)

    def test_cross_source_fusion_is_followed_by_final_content_rerank(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster/session.jsonl",
                session_id="session",
                model_client=FakeModelClient(),
                profile=ModelProfile("fake", context_window=1_000, max_output_tokens=20),
                config=config(),
            )
            broad = MemoryDocument(
                record_id="broad",
                category="project",
                content="exact words about a target and an unrelated phrase",
            )
            exact = MemoryDocument(
                record_id="exact",
                category="project",
                content="the exact target phrase is preserved here",
            )
            local_hits = [
                RetrievalHit(broad, 0.9, 0.02, 2.0, 0.3, 1, 2),
                RetrievalHit(exact, 0.8, 0.019, 1.8, 0.7, 2, 1),
            ]

            with (
                patch.object(manager.semantic, "search", return_value=local_hits),
                patch.object(manager, "search_archive", return_value=[]),
                patch.object(manager.episodic, "search", return_value=[]),
            ):
                items = manager.retrieve("exact target phrase")

            self.assertEqual(items[0].record_id, "exact")
            self.assertIn("cross_source_rrf_score", items[0].metadata)
            self.assertEqual(
                items[0].fused_score,
                items[0].metadata["final_rerank_score"],
            )

    def test_retrieved_memory_uses_fixed_fifteen_thousand_token_budget(self) -> None:
        items = [
            RetrievedMemoryItem(
                source="archive",
                record_id=f"parent-{index}",
                content=(("中文历史证据" if index == 0 else str(index)) * 50_000),
                fused_score=1.0,
                metadata={"source_path": "archive.jsonl"},
            )
            for index in range(2)
        ]

        rendered = MemoryManager._render_retrieval(items)

        self.assertLessEqual(estimate_retrieval_tokens(rendered), RETRIEVAL_TOKEN_BUDGET)
        self.assertIn("retrieval truncated", rendered)
        self.assertTrue(rendered.endswith("</retrieved_memory>"))

    def test_retrieval_budget_is_shared_across_ranked_items(self) -> None:
        items = [
            RetrievedMemoryItem(
                source="archive" if index < 5 else "episode",
                record_id=f"item-{index}",
                content=(f"evidence-{index} " * 10_000),
                fused_score=1.0 - index * 0.01,
                metadata={},
            )
            for index in range(11)
        ]

        rendered, trace, stats = MemoryManager._render_retrieval_with_trace(items)

        self.assertLessEqual(estimate_retrieval_tokens(rendered), RETRIEVAL_TOKEN_BUDGET)
        self.assertEqual(stats["rendered_count"], 11)
        self.assertEqual(len(trace), 11)
        self.assertIn("item-10", rendered)
        self.assertGreater(stats["truncated_count"], 0)

    def test_retrieval_trace_matches_the_evidence_rendered_for_the_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster/session.jsonl",
                session_id="session",
                model_client=FakeModelClient(),
                profile=ModelProfile("fake", context_window=1_000, max_output_tokens=20),
                config=config(),
            )
            items = [
                RetrievedMemoryItem(
                    source="archive",
                    record_id=f"item-{index}",
                    content=f"evidence-{index} " * 4_000,
                    fused_score=1.0 - index * 0.01,
                    metadata={"source_path": "archive.jsonl"},
                )
                for index in range(4)
            ]
            manager.last_retrieval = list(items)

            rendered = manager.render_retrieval(items, max_tokens=1_200)
            visible = manager.retrieval_trace()

            self.assertEqual(len(visible), manager.last_rendered_count)
            self.assertEqual(len(visible), manager.last_render_stats["rendered_count"])
            ranked = manager.ranked_retrieval_trace()
            self.assertEqual([item["record_id"] for item in ranked], [
                item.record_id for item in items
            ])
            self.assertEqual(ranked[0]["content"], items[0].content)
            for item in visible:
                self.assertIn(item["content"], rendered)
                self.assertTrue(item["metadata"]["truncated"])
            self.assertNotEqual(visible[0]["content"], items[0].content)

    def test_expired_procedure_candidates_are_not_retrieved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster/session.jsonl",
                session_id="session",
                model_client=FakeModelClient(),
                profile=ModelProfile("fake", context_window=1_000, max_output_tokens=20),
                config=config(),
            )
            manager.evidence.append(
                kind="procedure_candidate",
                content="Expired workflow\n1. inspect\n2. verify",
                session_id="old",
                expires_at="2020-01-01T00:00:00+00:00",
                metadata={"title": "Expired workflow"},
            )

            self.assertEqual(manager._procedure_candidates("Expired workflow", 3), [])


class MemoryToolArchiveSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_remember_records_provenance_used_by_automatic_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster/session.jsonl",
                session_id="source-session",
                model_client=FakeModelClient(),
                profile=ModelProfile("fake", context_window=1_000, max_output_tokens=20),
                config=config(),
                user_scope="user-1",
                channel_scope="channel-1",
            )
            memory_tool = manager.tools()[0]

            result = await memory_tool.execute(
                {
                    "action": "remember",
                    "category": "project",
                    "content": "Project codename is AURORA-7419",
                }
            )
            manager.evidence.append(
                kind="semantic",
                content="Project codename is AURORA-7419",
                session_id="source-session",
                source_path="session-transcript",
                user_scope="user-1",
                channel_scope="channel-1",
                workspace_scope=str(root),
                confidence=1.0,
                metadata={"category": "project", "evidence": "model extraction"},
            )
            items = manager.retrieve("AURORA-7419 codename")

            self.assertFalse(result.is_error)
            evidence = manager.evidence.records(kinds={"semantic"})
            self.assertEqual(len(evidence), 2)
            self.assertEqual(evidence[0].session_id, "source-session")
            semantic = next(item for item in items if item.source == "semantic")
            self.assertEqual(semantic.metadata["session_id"], "source-session")
            self.assertEqual(semantic.metadata["source_path"], str(manager.working.path))
            self.assertEqual(semantic.metadata["confidence"], 1.0)

    async def test_archive_search_returns_parent_context_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = ArchiveMemoryIndex(
                root / ".aster/memory/archive-index.jsonl",
                root,
                retriever=ArchiveMemoryTests.MarkerRetriever(),
                chunk_chars=400,
            )
            archive.add_messages(
                "session",
                "archive.jsonl",
                [
                    ChatMessage(
                        role="user",
                        content="\n".join(
                            [
                                "state-before " + ("a" * 300),
                                "tool-anchor " + ("b" * 300),
                                "state-after " + ("c" * 300),
                            ]
                        ),
                    )
                ],
            )
            tool = MemoryTool(
                SemanticMemoryStore(root / ".aster/memory/MEMORY.md"),
                EpisodicMemoryStore(root / ".aster/memory/episodes"),
                archive,
                "session",
            )

            result = await tool.execute(
                {"action": "archive_search", "query": "tool-anchor", "limit": 5}
            )
            payload = json.loads(result.content)

            self.assertEqual(len(payload), 1)
            self.assertIn("parentId", payload[0])
            self.assertEqual(payload[0]["chunkIndices"], [0, 1, 2])
            self.assertEqual(len(payload[0]["recordIds"]), 3)


class MemoryManagerCompactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_compaction_indexes_closed_history_and_updates_active_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster/session.jsonl",
                session_id="session",
                model_client=FakeModelClient(),
                profile=ModelProfile("fake", context_window=1_000, max_output_tokens=20),
                config=replace(config(), keep_recent_tokens=5),
            )
            messages = [
                ChatMessage(
                    role="user",
                    content="12. archived project was created in the country of Japan. " * 20,
                ),
                ChatMessage(role="assistant", content="stored old result " * 8),
                ChatMessage(role="user", content="current request"),
                ChatMessage(role="assistant", content="recent"),
            ]
            for message in messages:
                manager.append(message)
            outcome = await manager.maybe_compact(messages, 121)
            assert outcome is not None
            self.assertGreater(outcome.details["archive_indexed_chunks"], 0)
            self.assertEqual(manager.active_messages, outcome.messages)
            hits = manager.search_archive("archived project Japan", 5)
            self.assertTrue(any("archived project" in hit.content for hit in hits))


if __name__ == "__main__":
    unittest.main()
