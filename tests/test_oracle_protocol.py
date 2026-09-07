import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from MiniClaw.evaluation.oracle import decode_result, evaluate_oracle


class OracleProtocolTests(unittest.TestCase):
    def payload(self, passed=True):
        return {'protocol':'miniclaw-oracle-v1','status':'passed' if passed else 'capability_failure',
                'checks':[{'requirement_id':'R1','passed':passed,'evidence':'comparison of actual output'}]}

    def test_pass_and_capability_failure_are_distinct_from_invalid_measurement(self):
        for value,code,label in [(True,0,'passed'),(False,10,'capability_failure')]:
            self.assertEqual(decode_result(json.dumps(self.payload(value)),code,{'R1':'public rule'})['classification'],label)
        for data,code in [(self.payload(),10),({**self.payload(),'checks':[]},0),
                          ({**self.payload(),'checks':self.payload()['checks']*2},0)]:
            self.assertEqual(decode_result(json.dumps(data),code,{'R1':'public rule'})['classification'],'oracle_invalid')
        bad=self.payload();bad['checks'][0]['passed']='true'
        self.assertEqual(decode_result(json.dumps(bad),0,{'R1':'rule'})['classification'],'oracle_invalid')

    def test_frozen_hash_is_checked_before_executing_any_process(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'oracle.py';p.write_text('pass')
            with patch('MiniClaw.evaluation.oracle.subprocess.run') as run:
                result=evaluate_oracle({'script':str(p),'sha256':'wrong','requirements':{'R1':'rule'},'entry':'case'},Path(d))
                run.assert_not_called()
                self.assertEqual(result['classification'],'oracle_invalid')

    def test_docker_failure_is_not_an_agent_failure(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'oracle.py';p.write_text('pass')
            with patch('MiniClaw.evaluation.oracle.subprocess.run',side_effect=FileNotFoundError('docker unavailable')):
                result=evaluate_oracle({'script':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                                       'requirements':{'R1':'rule'},'entry':'case'},Path(d))
                self.assertEqual(result['classification'],'runtime_failure')
