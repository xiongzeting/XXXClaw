"""Counterexamples for graders, independent of generated application names."""
import json
import tempfile
import unittest
from pathlib import Path

from MiniClaw.evaluation.models import EvalCheck
from MiniClaw.evaluation.runner import evaluate_check, _parse_rubric_decision


class JudgeCalibrationTests(unittest.TestCase):
    def test_missing_phase_and_misspelled_assertion_do_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checks = [EvalCheck('final_not_contains', options={'phase':'absent','text':'bad'}),
                      EvalCheck('trace_event', options={'phase':'absent','event':'tool.call','exact_count':0}),
                      EvalCheck('trace_event', options={'event':'tool.call','count':0})]
            for check in checks:
                with self.subTest(check=check):
                    self.assertFalse(evaluate_check(check,root,root,{}, {'main':[{'type':'tool.call'}]})['passed'])

    def test_missing_artifact_never_passes_content_assertions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for kind, options in [('file_equals', {'text': ''}),
                                  ('file_not_contains', {'text': 'bad'}),
                                  ('file_regex', {'pattern': '^$'}),
                                  ('file_contains', {'text': ''})]:
                with self.subTest(kind=kind):
                    result = evaluate_check(EvalCheck(kind, options={'path': 'absent.txt', **options}), root, root, {}, {})
                    self.assertFalse(result['passed'])
            (root / 'empty.txt').write_text('')
            result = evaluate_check(EvalCheck('file_equals', options={'path': 'empty.txt', 'text': ''}), root, root, {}, {})
            self.assertTrue(result['passed'])

    def test_rubric_schema_rejects_truthy_strings_and_invalid_scores(self):
        invalid = [[], None, {}, {'passed': 'false', 'score': 1, 'reason': 'x'},
                   {'passed': 1, 'score': 1, 'reason': 'x'},
                   {'passed': True, 'score': True, 'reason': 'x'},
                   {'passed': True, 'score': '1', 'reason': 'x'},
                   {'passed': True, 'score': float('nan'), 'reason': 'x'},
                   {'passed': True, 'score': float('inf'), 'reason': 'x'},
                   {'passed': True, 'score': 1.1, 'reason': 'x'},
                   {'passed': True, 'score': -.1, 'reason': 'x'},
                   {'passed': True, 'score': .9, 'reason': []}]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                _parse_rubric_decision(json.dumps(payload))
        for passed in [False, True]:
            value = {'passed': passed, 'score': .75, 'reason': 'Evidence'}
            self.assertEqual(_parse_rubric_decision(json.dumps(value)), value)

    def test_exit_zero_does_not_override_failed_output_assertion(self):
        import sys
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = evaluate_check(EvalCheck('command', options={
                'command': [sys.executable, '-c', 'print("FAIL")'], 'output_contains': 'PASS'}), root, root, {}, {})
            self.assertFalse(result['passed'])
