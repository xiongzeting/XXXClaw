from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

from MiniClaw.coding_agent.memory.manager import MemoryManager
from MiniClaw.llm.types import (
    AssistantReply,
    ChatMessage,
    ModelEvent,
    ModelProfile,
    ModelRequest,
)


class ConsolidationModelClient:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        payload = {
            "episode_summary": "Database migration completed and verified.",
            "facts": [
                {
                    "category": "project",
                    "content": "Project database uses PostgreSQL.",
                    "evidence": "Project database uses PostgreSQL.",
                    "confidence": 0.97,
                }
            ],
            "procedures": [
                {
                    "title": "Verify database migrations",
                    "steps": ["Run the focused migration test", "Check exit code zero"],
                    "evidence": "Run the focused migration test, then check exit code zero.",
                    "confidence": 0.98,
                }
            ],
        }
        yield ModelEvent(
            type="completed",
            reply=AssistantReply(content=json.dumps(payload)),
        )


class MemoryDistillationTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_session_writes_only_evidence_backed_memories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = ConsolidationModelClient()
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster/session.jsonl",
                session_id="session",
                model_client=model,
                profile=ModelProfile("fake", context_window=8_000, max_output_tokens=2_048),
                environment={
                    "MINICLAW_MEMORY_ENABLED": "true",
                    "MINICLAW_MEMORY_CONSOLIDATION_ENABLED": "true",
                    "MINICLAW_MEMORY_VECTOR_ENABLED": "false",
                    "MINICLAW_MEMORY_CROSS_ENCODER_ENABLED": "false",
                },
            )
            messages = [
                ChatMessage(role="user", content="Project database uses PostgreSQL."),
                ChatMessage(
                    role="assistant",
                    content="Run the focused migration test, then check exit code zero.",
                ),
            ]

            result = await manager.finalize_session(messages, status="completed")

            self.assertTrue(result.model_used)
            self.assertEqual(result.facts_written, 1)
            self.assertEqual(result.procedure_candidates, 1)
            self.assertIn("PostgreSQL", manager.semantic.read())
            kinds = {record.kind for record in manager.evidence.records()}
            self.assertEqual(kinds, {"semantic", "procedure_candidate", "episode"})

    async def test_active_goal_attempt_does_not_run_model_or_mark_episode_completed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = ConsolidationModelClient()
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster/session.jsonl",
                session_id="session",
                model_client=model,
                profile=ModelProfile("fake", context_window=8_000, max_output_tokens=2_048),
                environment={
                    "MINICLAW_MEMORY_ENABLED": "true",
                    "MINICLAW_MEMORY_CONSOLIDATION_ENABLED": "true",
                    "MINICLAW_MEMORY_VECTOR_ENABLED": "false",
                    "MINICLAW_MEMORY_CROSS_ENCODER_ENABLED": "false",
                },
            )

            result = await manager.finalize_session(
                [ChatMessage(role="user", content="Continue the unfinished goal")],
                status="active",
            )

            self.assertFalse(result.model_used)
            self.assertEqual(model.requests, [])
            self.assertIn("active", manager.episodic.read("session"))

    async def test_one_run_benchmark_metrics_are_not_promoted_to_semantic_memory(self) -> None:
        class MetricModelClient:
            async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
                payload = {
                    "episode_summary": "A debug evaluation was completed.",
                    "facts": [
                        {
                            "category": "fact",
                            "content": "This debug benchmark passed 15/15 samples with a 100% success rate.",
                            "evidence": "The debug benchmark passed 15/15 samples with a 100% success rate.",
                            "confidence": 0.99,
                        }
                    ],
                    "procedures": [],
                }
                yield ModelEvent(
                    type="completed",
                    reply=AssistantReply(content=json.dumps(payload)),
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster/session.jsonl",
                session_id="session",
                model_client=MetricModelClient(),  # type: ignore[arg-type]
                profile=ModelProfile("fake", context_window=8_000, max_output_tokens=2_048),
                environment={
                    "MINICLAW_MEMORY_ENABLED": "true",
                    "MINICLAW_MEMORY_CONSOLIDATION_ENABLED": "true",
                    "MINICLAW_MEMORY_VECTOR_ENABLED": "false",
                    "MINICLAW_MEMORY_CROSS_ENCODER_ENABLED": "false",
                },
            )
            evidence = "The debug benchmark passed 15/15 samples with a 100% success rate."

            result = await manager.finalize_session(
                [ChatMessage(role="assistant", content=evidence)],
                status="completed",
            )

            self.assertEqual(result.facts_written, 0)
            self.assertEqual(result.facts_rejected, 1)
            self.assertNotIn("15/15", manager.semantic.read())

    async def test_model_conflict_keeps_existing_fact_and_records_inactive_observation(self) -> None:
        class ConflictModelClient:
            async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
                payload = {
                    "episode_summary": "A runtime discussion completed.",
                    "facts": [
                        {
                            "category": "project",
                            "content": "Project runtime uses host execution.",
                            "evidence": "Project runtime uses host execution.",
                            "confidence": 0.99,
                        }
                    ],
                    "procedures": [],
                }
                yield ModelEvent(
                    type="completed",
                    reply=AssistantReply(content=json.dumps(payload)),
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = MemoryManager(
                workspace=root,
                session_path=root / ".aster/session.jsonl",
                session_id="session",
                model_client=ConflictModelClient(),  # type: ignore[arg-type]
                profile=ModelProfile("fake", context_window=8_000, max_output_tokens=2_048),
                environment={
                    "MINICLAW_MEMORY_ENABLED": "true",
                    "MINICLAW_MEMORY_CONSOLIDATION_ENABLED": "true",
                    "MINICLAW_MEMORY_VECTOR_ENABLED": "false",
                    "MINICLAW_MEMORY_CROSS_ENCODER_ENABLED": "false",
                },
            )
            manager.semantic.remember("project", "Project runtime uses Docker execution.")

            result = await manager.finalize_session(
                [ChatMessage(role="user", content="Project runtime uses host execution.")],
                status="completed",
            )

            self.assertEqual(result.conflicts, 1)
            self.assertIn("Docker execution", manager.semantic.read())
            self.assertNotIn("host execution", manager.semantic.read())
            conflict = manager.evidence.records(kinds={"semantic_conflict"})[0]
            self.assertEqual(conflict.status, "inactive")


if __name__ == "__main__":
    unittest.main()
