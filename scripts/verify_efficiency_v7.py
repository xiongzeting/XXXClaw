"""Local interface/regression checks; leave earlier validation reports untouched."""
import hashlib
import io
import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]
MODULES = ['test_efficiency_revision_v5', 'test_efficiency_revision_v6', 'test_agent_loop', 'test_task_recovery']

CHANGED = ['agent/loop.py', 'coding_agent/verification_contract.py', 'coding_agent/assistant/progress.py',
           'coding_agent/assistant/verification.py', 'coding_agent/assistant/verification_evidence.py',
           'coding_agent/assistant/verification_rebind.py', 'coding_agent/assistant/coding.py']


def main():
    modules = ['test_efficiency_revision_v7', *MODULES]
    suite = unittest.defaultTestLoader.loadTestsFromNames(['tests.' + m for m in modules])
    stream = io.StringIO(); started = time.monotonic()
    result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
    folder = ROOT/'.aster/evals'; folder.mkdir(parents=True, exist_ok=True)
    (folder/'efficiency-revision-v7-validation.txt').write_text(stream.getvalue(), encoding='utf-8')
    data = {'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
            'skipped': len(result.skipped), 'success': result.wasSuccessful(),
            'seconds': round(time.monotonic()-started, 3), 'model_eval_requests': 0, 'modules': modules,
            'source_sha256': {p: hashlib.sha256((ROOT/'src/MiniClaw'/p).read_bytes()).hexdigest() for p in CHANGED}}
    (folder/'efficiency-revision-v7-validation.json').write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in data.items() if k not in {'modules', 'source_sha256'}}))
    if not result.wasSuccessful():
        print(stream.getvalue()); sys.exit(1)


if __name__ == '__main__': main()
