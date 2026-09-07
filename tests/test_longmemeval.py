from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from MiniClaw.benchmark.longmemeval import (
    LongMemEvalCase,
    answer_judge_prompt,
    answer_system_prompt,
    deterministic_answer_label,
    extract_session_ids,
    extract_turn_keys,
    history_messages,
    lexical_score,
    load_cases,
    retrieval_stress_score,
)


def _case(question_id: str = "case-1", question_type: str = "single-session-user"):
    return {
        "question_id": question_id,
        "question_type": question_type,
        "question": "What degree did I graduate with?",
        "answer": "Business Administration",
        "question_date": "2023/05/30 (Tue) 23:40",
        "haystack_session_ids": ["answer_session"],
        "haystack_dates": ["2023/05/20 (Sat) 02:21"],
        "haystack_sessions": [
            [
                {"role": "user", "content": "I graduated in Business Administration."},
                {"role": "assistant", "content": "Congratulations!"},
            ]
        ],
        "answer_session_ids": ["answer_session"],
    }


class LongMemEvalAdapterTests(unittest.TestCase):
    def test_history_turns_preserve_session_id_and_date(self) -> None:
        case = LongMemEvalCase(
            question_id="case-1",
            question_type="single-session-user",
            question="Question",
            answer="Answer",
            question_date="2023/05/30",
            haystack_session_ids=("session-1",),
            haystack_dates=("2023/05/20",),
            haystack_sessions=(({"role": "user", "content": "Evidence"},),),
            answer_session_ids=("session-1",),
        )
        messages = history_messages(case)
        self.assertEqual(len(messages), 1)
        self.assertIn('id="session-1"', messages[0].content)
        self.assertIn('date="2023/05/20"', messages[0].content)
        self.assertEqual(extract_session_ids(messages[0].content), {"session-1"})
        self.assertEqual(extract_turn_keys(messages[0].content), {"session-1:1"})

    def test_stratified_sampling_is_stable_and_includes_abstention(self) -> None:
        values = []
        for index, question_type in enumerate(
            (
                "single-session-user",
                "single-session-assistant",
                "single-session-preference",
                "multi-session",
                "knowledge-update",
                "temporal-reasoning",
            )
        ):
            values.append(_case(f"case-{index}", question_type))
        values.append(_case("case-abs_abs", "multi-session"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text(json.dumps(values), encoding="utf-8")
            first = load_cases(path, per_type=1, seed=7)
            second = load_cases(path, per_type=1, seed=7)
        self.assertEqual([item.question_id for item in first], [item.question_id for item in second])
        self.assertEqual(len(first), 7)
        self.assertIn("abstention", {item.category for item in first})

    def test_retrieval_stress_prefers_early_low_overlap_evidence(self) -> None:
        easy = LongMemEvalCase(
            question_id="easy",
            question_type="single-session-user",
            question="Which bedside lamp bulb did I replace?",
            answer="LED",
            question_date="2023/05/30",
            haystack_session_ids=("filler", "answer"),
            haystack_dates=("2023/05/01", "2023/05/29"),
            haystack_sessions=(
                ({"role": "user", "content": "unrelated"},),
                ({"role": "user", "content": "I replaced my bedside lamp bulb."},),
            ),
            answer_session_ids=("answer",),
        )
        hard = LongMemEvalCase(
            question_id="hard",
            question_type="single-session-user",
            question="Which bedside lamp bulb did I replace?",
            answer="LED",
            question_date="2023/05/30",
            haystack_session_ids=("answer", "filler"),
            haystack_dates=("2023/05/01", "2023/05/29"),
            haystack_sessions=(
                ({"role": "user", "content": "The warm Philips item is installed now."},),
                ({"role": "user", "content": "unrelated"},),
            ),
            answer_session_ids=("answer",),
        )
        self.assertGreater(retrieval_stress_score(hard), retrieval_stress_score(easy))

    def test_official_style_judge_and_lexical_diagnostics(self) -> None:
        case = LongMemEvalCase(
            question_id="case-1",
            question_type="knowledge-update",
            question="Where do I live?",
            answer="Seattle",
            question_date="2023/05/30",
            haystack_session_ids=(),
            haystack_dates=(),
            haystack_sessions=(),
            answer_session_ids=(),
        )
        prompt = answer_judge_prompt(case, "FINAL: I moved from Boston to Seattle.")
        self.assertIn("updated answer", prompt)
        score = lexical_score("FINAL: Business Administration", "Business Administration")
        self.assertTrue(score["lexical_exact"])
        self.assertTrue(score["lexical_contains_answer"])

    def test_preference_prompt_requires_cross_scenario_transfer(self) -> None:
        case = LongMemEvalCase(
            question_id="preference-1",
            question_type="single-session-preference",
            question="Suggest a Miami hotel",
            answer="Use prior hotel preferences",
            question_date="2023/05/30",
            haystack_session_ids=(),
            haystack_dates=(),
            haystack_sessions=(),
            answer_session_ids=(),
        )
        prompt = answer_system_prompt(case, "<retrieved_memory />")
        self.assertIn("transferable preference", prompt)
        self.assertIn("exact city", prompt)
        self.assertIn("general knowledge", prompt)
        self.assertIn("concrete candidates", prompt)
        self.assertIn("repeatedly across multiple turns", prompt)
        self.assertIn("at least one concrete retrieved user experience", prompt)
        self.assertIn("generic advice that could fit any user", prompt)

    def test_named_place_judge_accepts_optional_location_qualifier(self) -> None:
        case = LongMemEvalCase(
            question_id="assistant-place-1",
            question_type="single-session-assistant",
            question="What was the dessert shop we discussed?",
            answer="Cacao House at River Plaza",
            question_date="2023/05/30",
            haystack_session_ids=(),
            haystack_dates=(),
            haystack_sessions=(),
            answer_session_ids=(),
        )

        prompt = answer_judge_prompt(case, "FINAL: Cacao House")

        self.assertIn("canonical name alone is equivalent", prompt)
        self.assertIn("location qualifier", prompt)

    def test_count_questions_accept_number_word_equivalence_after_judge_false_negative(self) -> None:
        case = LongMemEvalCase(
            question_id="count-1",
            question_type="multi-session",
            question="How many different doctors did I visit?",
            answer="I visited three different doctors.",
            question_date="2023/05/30",
            haystack_session_ids=(),
            haystack_dates=(),
            haystack_sessions=(),
            answer_session_ids=(),
        )
        self.assertTrue(
            deterministic_answer_label(case, "FINAL: You visited 3 doctors: A, B, and C.")
        )
        prompt = answer_system_prompt(case, "<retrieved_memory />")
        self.assertIn("coverage question", prompt)
        self.assertIn("archive_search at least once", prompt)

    def test_coverage_prompt_separates_completed_user_facts_from_future_suggestions(self) -> None:
        case = LongMemEvalCase(
            question_id="coverage-actuality-1",
            question_type="multi-session",
            question="How much time did I spend traveling to all three places?",
            answer="12 hours",
            question_date="2023/05/30",
            haystack_session_ids=(),
            haystack_dates=(),
            haystack_sessions=(),
            answer_session_ids=(),
        )

        prompt = answer_system_prompt(case, "<retrieved_memory />")

        self.assertIn("actor, actuality, time/status, and value", prompt)
        self.assertIn("explicitly reports as completed", prompt)
        self.assertIn("future plans", prompt)
        self.assertIn("assistant-provided estimates", prompt)
        self.assertIn("completed user history", prompt)

    def test_temporal_prompt_binds_relative_time_to_the_queried_event(self) -> None:
        case = LongMemEvalCase(
            question_id="temporal-1",
            question_type="temporal-reasoning",
            question="How many days ago did I meet Emma?",
            answer="9 days ago",
            question_date="2023/04/20",
            haystack_session_ids=(),
            haystack_dates=(),
            haystack_sessions=(),
            answer_session_ids=(),
        )

        prompt = answer_system_prompt(case, "<retrieved_memory />")

        self.assertIn("exact event clause", prompt)
        self.assertIn("never transfer 'today'", prompt)
        self.assertIn("session timestamp first", prompt)

    def test_summary_only_prompt_does_not_request_unavailable_archive_search(self) -> None:
        case = LongMemEvalCase(
            question_id="summary-only-1",
            question_type="multi-session",
            question="What is the order of all events?",
            answer="A then B",
            question_date="2023/05/30",
            haystack_session_ids=(),
            haystack_dates=(),
            haystack_sessions=(),
            answer_session_ids=(),
        )

        prompt = answer_system_prompt(case, "", retrieval_available=False)

        self.assertIn("summary-only baseline", prompt)
        self.assertIn("archive search is unavailable", prompt)
        self.assertNotIn("Call memory archive_search", prompt)


if __name__ == "__main__":
    unittest.main()
