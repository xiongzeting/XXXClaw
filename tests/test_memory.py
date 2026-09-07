from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

from MiniClaw.llm.types import (
    AssistantReply,
    ChatMessage,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    ToolInvocation,
)
from MiniClaw.coding_agent.memory.artifacts import ARTIFACT_MARKER
from MiniClaw.coding_agent.memory.config import MemoryConfig
from MiniClaw.coding_agent.memory.episodic import EpisodicMemoryStore
from MiniClaw.coding_agent.memory.procedural import ProceduralMemoryStore
from MiniClaw.coding_agent.memory.retrieval import RetrievalHit
from MiniClaw.coding_agent.memory.semantic import SemanticMemoryStore
from MiniClaw.coding_agent.memory.working import CHECKPOINT_MARKER, WorkingContext
from MiniClaw.coding_agent.tools.base import ToolResult


class SummaryClient:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        yield ModelEvent(
            type="completed",
            reply=AssistantReply(content="## Goal\nContinue the archived task."),
        )


class EmptySummaryClient(SummaryClient):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        yield ModelEvent(type="completed", reply=AssistantReply(content="", stop_reason="length"))


def memory_config(**overrides: object) -> MemoryConfig:
    values: dict[str, object] = {
        "enabled": True,
        "reserve_tokens": 20,
        "keep_recent_tokens": 20,
        "soft_trigger_tokens": 100,
        "hard_trigger_tokens": 120,
        "target_tokens": 80,
        "progressive_enabled": True,
        "artifact_threshold_bytes": 32,
        "artifact_preview_chars": 12,
        "deterministic_semantic_tokens": 1_000,
    }
    values.update(overrides)
    return MemoryConfig(**values)  # type: ignore[arg-type]


class WorkingContextTests(unittest.IsolatedAsyncioTestCase):
    def make_context(self, root: Path, **config: object) -> WorkingContext:
        return WorkingContext(
            path=root / ".aster" / "session.jsonl",
            workspace=root,
            session_id="test",
            config=memory_config(**config),
            model_client=SummaryClient(),
            profile=ModelProfile("fake", context_window=1_000, max_output_tokens=20),
        )

    async def test_large_live_tool_result_becomes_recoverable_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.make_context(root)
            result = await context.artifactize_live_result(
                ToolInvocation("call-1", "bash", {}),
                ToolResult("x" * 100),
            )
            self.assertIn(ARTIFACT_MARKER, result.content)
            artifact = result.details["context_artifact"]
            self.assertEqual((root / artifact["path"]).read_text(encoding="utf-8"), "x" * 100)

    async def test_legacy_arm_skips_live_artifactization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.make_context(root, strategy="legacy-summary-recent")
            result = await context.artifactize_live_result(
                ToolInvocation("call-1", "bash", {}),
                ToolResult("x" * 100),
            )
            self.assertEqual(result.content, "x" * 100)
            self.assertNotIn("context_artifact", result.details)

    async def test_legacy_arm_waits_for_hard_limit_and_only_summarizes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.make_context(
                root,
                strategy="legacy-summary-recent",
                deterministic_semantic_tokens=1,
            )
            messages = [
                ChatMessage(role="user", content="old request " * 20),
                ChatMessage(role="assistant", content="old answer " * 20),
                ChatMessage(role="user", content="current request"),
                ChatMessage(role="assistant", content="recent answer"),
            ]
            for message in messages:
                context.append_message(message)

            self.assertIsNone(await context.maybe_compact(messages, provider_input_tokens=110))
            outcome = await context.maybe_compact(messages, provider_input_tokens=121)

            assert outcome is not None
            self.assertEqual(outcome.strategy, "model-summary")
            self.assertEqual(outcome.details["archive"], None)
            self.assertEqual(
                outcome.details["layers_applied"],
                ["recent-original", "model-summary"],
            )
            self.assertNotIn("Archived Source", outcome.messages[0].content)
            artifact_root = root / ".aster" / "context-artifacts"
            self.assertFalse(artifact_root.exists())

    async def test_compaction_is_append_only_and_recovers_checkpoint_plus_recent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.make_context(root)
            messages = [
                ChatMessage(role="user", content="old request " * 120),
                ChatMessage(role="assistant", content="old answer " * 120),
                ChatMessage(role="user", content="current exact request"),
                ChatMessage(role="assistant", content="recent result"),
            ]
            for message in messages:
                context.append_message(message)
            outcome = await context.maybe_compact(messages, provider_input_tokens=121)
            self.assertIsNotNone(outcome)
            assert outcome is not None
            self.assertIn(CHECKPOINT_MARKER, outcome.messages[0].content)
            self.assertEqual(outcome.messages[1].content, "current exact request")
            event_types = [
                json.loads(line)["type"]
                for line in context.path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(event_types.count("message"), 4)
            self.assertEqual(event_types[-1], "compaction")

            recovered_context = self.make_context(root)
            recovered = recovered_context.load()
            self.assertEqual(recovered[0].role, "assistant")
            self.assertIn(CHECKPOINT_MARKER, recovered[0].content)
            self.assertEqual(recovered[1].content, "current exact request")
            self.assertEqual(recovered[2].content, "recent result")
            self.assertEqual(
                json.loads(context.path.read_text(encoding="utf-8").splitlines()[-1])["type"],
                "recovery",
            )

    async def test_tool_call_and_result_stay_on_same_side_of_cut(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.make_context(root, keep_recent_tokens=10)
            messages = [
                ChatMessage(role="user", content="old" * 300),
                ChatMessage(
                    role="assistant",
                    tool_calls=[ToolInvocation("call-1", "read", {"path": "a.txt"})],
                ),
                ChatMessage(
                    role="tool",
                    content="tool output" * 10,
                    tool_call_id="call-1",
                    name="read",
                ),
                ChatMessage(role="user", content="new request"),
                ChatMessage(role="assistant", content="new answer"),
            ]
            for message in messages:
                context.append_message(message)
            outcome = await context.maybe_compact(messages, provider_input_tokens=121)
            assert outcome is not None
            roles = [message.role for message in outcome.messages]
            self.assertEqual(roles, ["assistant", "user", "assistant"])
            archive = root / outcome.details["archive"]["path"]
            archived_roles = [json.loads(line)["role"] for line in archive.read_text().splitlines()]
            self.assertEqual(archived_roles, ["user", "assistant", "tool"])

    async def test_hard_compaction_uses_model_when_semantic_history_is_dense(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = SummaryClient()
            context = WorkingContext(
                path=root / "session.jsonl",
                workspace=root,
                session_id="test",
                config=memory_config(deterministic_semantic_tokens=1),
                model_client=client,
                profile=ModelProfile("fake", context_window=1_000, max_output_tokens=20),
            )
            messages = [
                ChatMessage(role="user", content="old semantic request" * 20),
                ChatMessage(role="assistant", content="old semantic answer" * 20),
                ChatMessage(role="user", content="current"),
                ChatMessage(role="assistant", content="recent"),
            ]
            for message in messages:
                context.append_message(message)
            outcome = await context.maybe_compact(messages, provider_input_tokens=121)
            assert outcome is not None
            self.assertEqual(outcome.strategy, "model-summary")
            self.assertEqual(len(client.requests), 1)

    async def test_hard_compaction_falls_back_when_model_summary_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = EmptySummaryClient()
            context = WorkingContext(
                path=root / "session.jsonl",
                workspace=root,
                session_id="test",
                config=memory_config(deterministic_semantic_tokens=1),
                model_client=client,
                profile=ModelProfile("fake", context_window=1_000, max_output_tokens=20),
            )
            messages = [
                ChatMessage(role="user", content="old semantic request" * 200),
                ChatMessage(role="assistant", content="old semantic answer" * 200),
                ChatMessage(role="user", content="current"),
                ChatMessage(role="assistant", content="recent"),
            ]
            for message in messages:
                context.append_message(message)
            outcome = await context.maybe_compact(messages, provider_input_tokens=121)
            assert outcome is not None
            self.assertEqual(outcome.strategy, "deterministic-fallback")
            self.assertEqual(outcome.details["model_summary_error"], "context summarization failed")
            self.assertIn("deterministic-summary-fallback", outcome.details["layers_applied"])
            self.assertIn("Exact archive:", outcome.messages[0].content)

    async def test_second_compaction_merges_previous_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = self.make_context(root)
            first_messages = [
                ChatMessage(role="user", content="first old request " * 120),
                ChatMessage(role="assistant", content="first old answer " * 120),
                ChatMessage(role="user", content="first current request"),
                ChatMessage(role="assistant", content="first current answer"),
            ]
            for message in first_messages:
                context.append_message(message)
            first = await context.maybe_compact(first_messages, provider_input_tokens=121)
            assert first is not None

            second_messages = [
                *first.messages,
                ChatMessage(role="user", content="second old request " * 120),
                ChatMessage(role="assistant", content="second old answer " * 120),
                ChatMessage(role="user", content="second current request"),
                ChatMessage(role="assistant", content="second current answer"),
            ]
            for message in second_messages[-4:]:
                context.append_message(message)
            # Safety pressure bypasses the new minimum interval between compactions.
            second = await context.maybe_compact(second_messages, provider_input_tokens=context.profile.context_window)
            assert second is not None
            self.assertIn("second current request", [message.content for message in second.messages])
            client = context.model_client
            self.assertIsInstance(client, SummaryClient)
            assert isinstance(client, SummaryClient)
            update_prompt = client.requests[-1].messages[-1].content
            self.assertIn("<previous-summary>", update_prompt)
            self.assertIn("first current request", update_prompt)
            compactions = [
                json.loads(line)
                for line in context.path.read_text(encoding="utf-8").splitlines()
                if json.loads(line).get("type") == "compaction"
            ]
            self.assertEqual(len(compactions), 2)


class LongTermMemoryTests(unittest.TestCase):
    def test_semantic_memory_rejects_secrets_transients_duplicates_and_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SemanticMemoryStore(Path(directory) / "MEMORY.md")
            store.remember("project", "language: Python")
            self.assertEqual(store.remember("project", "language: Python"), "Memory already exists")
            with self.assertRaisesRegex(ValueError, "conflict"):
                store.remember("project", "language: Rust")
            with self.assertRaisesRegex(ValueError, "secret"):
                store.remember("environment", "api_key: redacted-test-value")
            with self.assertRaisesRegex(ValueError, "transient"):
                store.remember("fact", "本轮命令测试通过")

    def test_episode_search_and_skill_progressive_loading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            episodes = EpisodicMemoryStore(root / "episodes")
            episodes.checkpoint(
                "login-fix",
                [ChatMessage(role="user", content="repair oauth login")],
                summary="OAuth callback repaired.",
                status="completed",
            )
            self.assertEqual(episodes.search("OAuth")[0]["sessionId"], "login-fix")

            skill_root = root / "skills"
            skill = skill_root / "testing"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: testing\ndescription: Run focused tests.\n---\nUse unittest.",
                encoding="utf-8",
            )
            registry = ProceduralMemoryStore(skill_root)
            self.assertIn("testing: Run focused tests.", registry.catalog_for_prompt())
            self.assertIn("Use unittest.", registry.read("testing"))
            with self.assertRaisesRegex(ValueError, "inside"):
                registry.read("testing", "../outside.txt")

    def test_episode_search_uses_the_shared_hybrid_retriever(self) -> None:
        class RecordingRetriever:
            def __init__(self) -> None:
                self.calls: list[tuple[str, int, int]] = []

            def search(self, query, documents, limit):
                self.calls.append((query, len(documents), limit))
                target = next(
                    document for document in documents if document.record_id == "lock-fix"
                )
                return [
                    RetrievalHit(
                        document=target,
                        score=0.9,
                        rrf_score=0.02,
                        bm25_score=1.5,
                        vector_score=0.8,
                        bm25_rank=1,
                        vector_rank=2,
                    )
                ]

        with tempfile.TemporaryDirectory() as directory:
            retriever = RecordingRetriever()
            episodes = EpisodicMemoryStore(Path(directory), retriever=retriever)
            episodes.checkpoint(
                "lock-fix",
                [ChatMessage(role="user", content="repair concurrent inventory locking")],
                summary="Use a PostgreSQL transaction and row lock.",
                status="completed",
            )
            episodes.checkpoint(
                "login-fix",
                [ChatMessage(role="user", content="repair OAuth login")],
                summary="OAuth callback repaired.",
                status="completed",
            )

            results = episodes.search("database concurrency", 5)

            self.assertEqual(retriever.calls, [("database concurrency", 2, 5)])
            self.assertEqual(results[0]["sessionId"], "lock-fix")
            self.assertIn("PostgreSQL transaction", str(results[0]["content"]))
            self.assertEqual(results[0]["bm25Rank"], 1)


if __name__ == "__main__":
    unittest.main()
