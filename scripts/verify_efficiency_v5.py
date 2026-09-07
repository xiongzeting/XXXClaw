"""Fast, local validation of v5 runtime changes; no real model eval calls."""
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
MODULES=[
    'test_efficiency_revision_v5','test_acceptance_contracts','test_progress_protocol',
    'test_request_context','test_context_efficiency','test_efficiency_revision_v3',
    'test_task_recovery','test_memory_cost','test_agent_loop','test_architecture',
    'test_tools','test_runtime','test_approval','test_coding_prompt','test_delivery',
    'test_memory','test_archive_memory','test_memory_retrieval','test_incremental_consolidation',
    'test_instructions','test_goal','test_local_agent_sessions','test_execution_evidence',
    'test_eval_path_audit','test_trace','test_cancellation_e2e',
]
CHANGED=[
    'agent/loop.py','coding_agent/verification_contract.py','coding_agent/assistant/progress.py',
    'coding_agent/assistant/verification.py','coding_agent/assistant/prompts.py',
    'coding_agent/assistant/coding.py','coding_agent/tools/bash.py','coding_agent/tools/executor.py',
    'coding_agent/memory/working.py','coding_agent/memory/history_budget.py',
]

def main():
    suite=unittest.defaultTestLoader.loadTestsFromNames(['tests.'+m for m in MODULES])
    stream=io.StringIO();started=time.monotonic()
    result=unittest.TextTestRunner(stream=stream,verbosity=1).run(suite)
    folder=ROOT/'.aster/evals';folder.mkdir(parents=True,exist_ok=True)
    (folder/'efficiency-revision-v5-validation.txt').write_text(stream.getvalue(),encoding='utf-8')
    data={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
          'skipped':len(result.skipped),'success':result.wasSuccessful(),'seconds':round(time.monotonic()-started,3),
          'model_eval_requests':0,'modules':MODULES,
          'source_sha256':{p:hashlib.sha256((ROOT/'src/MiniClaw'/p).read_bytes()).hexdigest() for p in CHANGED}}
    (folder/'efficiency-revision-v5-validation.json').write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in data.items() if k not in {'modules','source_sha256'}}))
    if not result.wasSuccessful():
        print(stream.getvalue());sys.exit(1)

if __name__=='__main__':main()
