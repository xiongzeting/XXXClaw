from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from pathlib import Path

from MiniClaw.benchmark.longmemeval_v2 import (
    MEMORY_CONTEXT_TOKEN_BUDGET,
    QUESTION_TYPES,
    RetrievedParent,
    SelectedQuestion,
    TrajectoryMemoryIndex,
    TrajectoryReadTool,
    TrajectorySearchSession,
    TrajectorySearchTool,
    build_trajectory_offsets,
    build_parser,
    select_questions_by_id,
    normalize_presentational_latex,
    read_trajectory,
    render_memory_context,
    retrieve_case,
    select_questions,
    trajectory_documents,
    run_agentic_search_case,
)
from MiniClaw.coding_agent.memory.manager import estimate_retrieval_tokens
from MiniClaw.llm.types import (
    AssistantReply,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    ToolInvocation,
)
from MiniClaw.trace.store import TraceRecorder, read_trace_records


class LongMemEvalV2AdapterTests(unittest.TestCase):
    def test_presentational_latex_is_removed_before_deterministic_judging(self) -> None:
        self.assertEqual(
            normalize_presentational_latex(r"\text{Results, Uses}"),
            "Results, Uses",
        )
        self.assertEqual(normalize_presentational_latex(r"\mathrm{UNKNOWN}"), "UNKNOWN")

    def test_selection_covers_every_stratum_without_images(self) -> None:
        rows = []
        for domain in ("enterprise", "web"):
            for question_type in QUESTION_TYPES:
                for index in range(5):
                    rows.append(
                        {
                            "id": f"{domain}-{question_type}-{index}",
                            "domain": domain,
                            "question_type": question_type,
                            "question": "question",
                            "answer": "answer",
                            "eval_function": "norm_phrase_set_match",
                            "image": "image.png" if index == 4 else None,
                        }
                    )
        selected = select_questions(rows, limit=40, seed=7, text_only=True)
        self.assertEqual(len(selected), 40)
        self.assertEqual(
            {(item.domain, item.question_type) for item in selected},
            {
                (domain, question_type)
                for domain in ("enterprise", "web")
                for question_type in QUESTION_TYPES
                if question_type != "errors-gotchas"
            },
        )
        self.assertFalse(any(item.question_id.endswith("-4") for item in selected))

    def test_binary_offset_index_reads_only_requested_trajectories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "trajectories.jsonl"
            values = [
                {"id": "a", "states": []},
                {"id": "b", "states": [{"state_index": 0}]},
            ]
            path.write_text(
                "".join(json.dumps(value, separators=(",", ":")) + "\n" for value in values),
                encoding="utf-8",
            )
            offsets = build_trajectory_offsets(path, {"b"}, cache_path=root / "offsets.json")
            with path.open("rb") as handle:
                self.assertEqual(read_trajectory(handle, "b", offsets)["id"], "b")

    def test_trajectory_documents_deduplicate_repeated_state_content(self) -> None:
        trajectory = {
            "id": "t1",
            "environment": "test",
            "goal": "find a value",
            "outcome": "success",
            "states": [
                {"state_index": 0, "thought": "look", "accessibility_tree": "same tree"},
                {"state_index": 1, "thought": "look", "accessibility_tree": "same tree"},
            ],
        }
        documents, metadata = trajectory_documents(trajectory, chunk_chars=400)
        contents = [document.content for document in documents]
        self.assertEqual(contents.count("same tree"), 1)
        self.assertEqual(set(metadata), {document.record_id for document in documents})

    def test_retrieval_selects_parent_and_expands_adjacent_states(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "trajectories.jsonl"
            trajectory = {
                "id": "trajectory-one",
                "environment": "test",
                "goal": "change a setting",
                "outcome": "success",
                "states": [
                    {"state_index": 10, "action": "observe before-state"},
                    {
                        "state_index": 20,
                        "action": "apply unique-needle setting",
                        "accessibility_tree": "unique-needle current-state",
                    },
                    {"state_index": 30, "action": "verify after-state"},
                ],
            }
            path.write_text(json.dumps(trajectory, separators=(",", ":")) + "\n", encoding="utf-8")
            offsets = build_trajectory_offsets(
                path,
                {"trajectory-one"},
                cache_path=root / "offsets-one.json",
            )
            question = SelectedQuestion(
                question_id="q1",
                domain="web",
                question_type="procedure",
                question="unique-needle",
                answer="answer",
                eval_function="norm_phrase_set_match",
            )

            hits, stats = retrieve_case(
                question=question,
                trajectory_ids=["trajectory-one"],
                trajectory_path=path,
                offsets=offsets,
                top_k=5,
                chunk_chars=400,
            )

            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].state_indices, (10, 20, 30))
            self.assertIn("before-state", hits[0].content)
            self.assertIn("after-state", hits[0].content)
            self.assertEqual(stats["parent_count"], 1)

    def test_retrieval_caps_distinct_parent_states_at_five(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "trajectories.jsonl"
            trajectory = {
                "id": "trajectory-many",
                "environment": "test",
                "goal": "inspect states",
                "outcome": "success",
                "states": [
                    {"state_index": index, "thought": f"shared-needle state {index}"}
                    for index in range(8)
                ],
            }
            path.write_text(json.dumps(trajectory, separators=(",", ":")) + "\n", encoding="utf-8")
            offsets = build_trajectory_offsets(
                path,
                {"trajectory-many"},
                cache_path=root / "offsets-many.json",
            )
            question = SelectedQuestion(
                question_id="q2",
                domain="web",
                question_type="dynamic-environment",
                question="shared-needle",
                answer="answer",
                eval_function="norm_phrase_set_match",
            )

            hits, _ = retrieve_case(
                question=question,
                trajectory_ids=["trajectory-many"],
                trajectory_path=path,
                offsets=offsets,
                top_k=20,
                chunk_chars=400,
            )

            self.assertEqual(len(hits), 5)
            self.assertEqual(len({hit.parent_id for hit in hits}), 5)

    def test_rendered_parent_context_is_limited_to_fifteen_thousand_tokens(self) -> None:
        hits = [
            RetrievedParent(
                parent_id=f"parent-{index}",
                trajectory_id=f"trajectory-{index}",
                anchor_state_index=index,
                anchor_record_id=f"record-{index}",
                record_ids=(f"record-{index}",),
                state_indices=(index,),
                content=(("状态证据" if index == 0 else str(index)) * 30_000),
                score=1.0,
                bm25_score=1.0,
                vector_score=0.0,
                bm25_rank=index + 1,
                vector_rank=None,
            )
            for index in range(5)
        ]

        rendered = render_memory_context(hits)

        self.assertLessEqual(estimate_retrieval_tokens(rendered), MEMORY_CONTEXT_TOKEN_BUDGET)
        self.assertEqual(rendered.count("[Evidence parent"), 5)

    def test_cli_defaults_to_agentic_search_and_preserves_one_shot_switch(self) -> None:
        parser = build_parser()
        enabled = parser.parse_args(["--official-repo", "repo", "--data-root", "data"])
        disabled = parser.parse_args(
            ["--official-repo", "repo", "--data-root", "data", "--no-agentic-search"]
        )
        self.assertTrue(enabled.agentic_search)
        self.assertFalse(disabled.agentic_search)

    def test_explicit_question_ids_do_not_depend_on_seed_limit_selection(self) -> None:
        rows = [
            {
                "id": "question-a",
                "domain": "web",
                "question_type": "procedure",
                "question": "A?",
                "answer": "A",
                "eval_function": "exact",
                "image": None,
            },
            {
                "id": "question-b",
                "domain": "enterprise",
                "question_type": "static-environment",
                "question": "B?",
                "answer": "B",
                "eval_function": "exact",
                "image": None,
            },
        ]

        selected = select_questions_by_id(
            rows, ["question-b", "question-a"], text_only=True
        )

        self.assertEqual([item.question_id for item in selected], ["question-b", "question-a"])
        args = build_parser().parse_args(
            [
                "--official-repo",
                "repo",
                "--data-root",
                "data",
                "--question-id-file",
                "heldout.ids",
            ]
        )
        self.assertEqual(args.question_id_file, "heldout.ids")


class LongMemEvalV2AgenticTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _index(root: Path) -> TrajectoryMemoryIndex:
        path = root / "trajectories.jsonl"
        trajectory = {
            "id": "trajectory-one",
            "environment": "test",
            "goal": "change a setting",
            "outcome": "success",
            "states": [
                {"state_index": 10, "action": "observe old blue setting"},
                {
                    "state_index": 20,
                    "action": "apply unique-needle setting",
                    "accessibility_tree": "unique-needle final value is green",
                },
                {"state_index": 30, "action": "verify green after-state"},
            ],
        }
        path.write_text(
            json.dumps(trajectory, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        offsets = build_trajectory_offsets(
            path,
            {"trajectory-one"},
            cache_path=root / "offsets.json",
        )
        return TrajectoryMemoryIndex(
            trajectory_ids=["trajectory-one"],
            trajectory_path=path,
            offsets=offsets,
            chunk_chars=400,
        )

    async def test_search_discovers_parent_and_read_rejects_hidden_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session = TrajectorySearchSession(self._index(Path(temporary)))
            hidden = await TrajectoryReadTool(session).execute({"parent_id": "hidden"})
            self.assertTrue(hidden.is_error)
            searched = await TrajectorySearchTool(session).execute(
                {"query": "unique-needle", "limit": 2}
            )
            payload = json.loads(searched.content)
            read = await TrajectoryReadTool(session).execute(
                {"parent_id": payload[0]["parent_id"]}
            )
            self.assertFalse(read.is_error)
            self.assertIn("green", read.content)

    async def test_tool_budgets_limit_search_read_and_total_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session = TrajectorySearchSession(
                self._index(Path(temporary)),
                max_searches=1,
                max_reads=1,
                evidence_token_budget=20,
            )
            search_tool = TrajectorySearchTool(session)
            first = await search_tool.execute({"query": "unique-needle"})
            second = await search_tool.execute({"query": "green"})
            self.assertFalse(first.is_error)
            self.assertTrue(second.is_error)
            parent_id = json.loads(first.content)[0]["parent_id"]
            read_tool = TrajectoryReadTool(session)
            read = await read_tool.execute({"parent_id": parent_id})
            duplicate = await read_tool.execute({"parent_id": parent_id})
            self.assertLessEqual(session.evidence_tokens_used, 20)
            self.assertLessEqual(estimate_retrieval_tokens(read.content), 20)
            self.assertTrue(read.details["truncated"])
            self.assertTrue(duplicate.is_error)

    async def test_agent_loop_can_search_then_read_and_accumulate_usage(self) -> None:
        class ScriptedClient:
            def __init__(self, parent_id: str) -> None:
                self.parent_id = parent_id
                self.requests: list[ModelRequest] = []

            async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
                self.requests.append(request)
                turn = len(self.requests)
                if turn == 1:
                    reply = AssistantReply(
                        tool_calls=[
                            ToolInvocation(
                                "search-1",
                                "trajectory_search",
                                {"query": "unique-needle current value"},
                            )
                        ],
                        stop_reason="tool_calls",
                    )
                    reply.usage.input_tokens = 100
                elif turn == 2:
                    reply = AssistantReply(
                        tool_calls=[
                            ToolInvocation(
                                "read-1",
                                "trajectory_read",
                                {"parent_id": self.parent_id},
                            )
                        ],
                        stop_reason="tool_calls",
                    )
                    reply.usage.input_tokens = 120
                else:
                    reply = AssistantReply(content=r"\boxed{green}")
                    reply.usage.input_tokens = 140
                    reply.usage.output_tokens = 8
                yield ModelEvent(type="completed", reply=reply)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self._index(root)
            parent_id = index.search("unique-needle", limit=1)[0][0].parent_id
            recorder = TraceRecorder(root, "benchmark", "case")
            question = SelectedQuestion(
                "q1", "web", "dynamic-environment", "What is the value?", "green", "exact"
            )
            response, error, usage, hits, stats = await run_agentic_search_case(
                question=question,
                index=index,
                client=ScriptedClient(parent_id),
                profile=ModelProfile("fake"),
                recorder=recorder,
                run_id="run-1",
                max_searches=3,
                max_reads=5,
                evidence_token_budget=15_000,
                max_agent_turns=8,
            )
            self.assertEqual(response, r"\boxed{green}")
            self.assertEqual(error, "")
            self.assertEqual(usage.input_tokens, 360)
            self.assertEqual(usage.output_tokens, 8)
            self.assertEqual(stats["search_calls"], 1)
            self.assertEqual(stats["read_calls"], 1)
            self.assertTrue(hits)
            tool_records = [
                record
                for record in read_trace_records(recorder.path)
                if record["type"] == "tool.call"
            ]
            self.assertEqual([item["data"]["tool"] for item in tool_records], [
                "trajectory_search",
                "trajectory_read",
            ])


if __name__ == "__main__":
    unittest.main()
