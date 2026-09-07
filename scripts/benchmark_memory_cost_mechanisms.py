"""Compare fixed synthetic transcripts in isolated snapshot interpreters, offline."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CHILD = r'''
import asyncio, json, sys, tempfile
from pathlib import Path
import MiniClaw.coding_agent.memory.working as working
from MiniClaw.coding_agent.memory.config import MemoryConfig
from MiniClaw.llm.types import AssistantReply, ChatMessage, ModelEvent, ModelProfile

assert Path(working.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
class FixedClient:
    def __init__(self): self.calls = 0
    async def stream(self, request):
        self.calls += 1
        yield ModelEvent(type='completed', reply=AssistantReply(content='Preserve output bytes on errors; complete migration.'))

def context(root, client):
    config = MemoryConfig(enabled=True, reserve_tokens=1024, keep_recent_tokens=200,
        soft_trigger_tokens=2000, hard_trigger_tokens=3000, target_tokens=1600,
        progressive_enabled=True, artifact_threshold_bytes=4096, artifact_preview_chars=300,
        deterministic_semantic_tokens=200)
    return working.WorkingContext(path=root/'session.jsonl', workspace=root,
        session_id='mechanism', config=config, model_client=client,
        profile=ModelProfile('offline', context_window=16000, max_output_tokens=1024))

async def main():
    result = {}
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder); client = FixedClient(); w = context(root, client)
        messages = [ChatMessage(role='user', content='Old dense migration constraint ' * 250),
                    ChatMessage(role='assistant', content='Completed work ' * 200),
                    ChatMessage(role='user', content='Latest request'),
                    ChatMessage(role='assistant', content='Retained analysis ' * 60)]
        for m in messages: w.append_message(m)
        before = w.path.read_bytes()
        for _ in range(3): assert await w.maybe_compact(messages, 2500) is None
        assert w.path.read_bytes() == before
        result['soft_deferral'] = {'attempts': 3, 'model_calls': client.calls,
            'artifact_files': sum(p.is_file() for p in w.artifacts.root.rglob('*')),
            'original_transcript_unchanged': True}
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder); client = FixedClient(); w = context(root, client)
        latest = 'UNIQUE_RETAINED_REQUEST: ' + 'unchanged current request ' * 240
        messages = [ChatMessage(role='user', content='Preserve output bytes on errors ' * 500),
                    ChatMessage(role='assistant', content='Old implementation analysis ' * 200),
                    ChatMessage(role='user', content=latest),
                    ChatMessage(role='assistant', content='Understood')]
        for m in messages: w.append_message(m)
        before = working.estimate_context_tokens(messages)
        outcome = await w.maybe_compact(messages, 10000)
        assert outcome is not None, 'Both arms must really compact'
        recovered = context(root, FixedClient()).load()
        assert [m.content for m in recovered] == [m.content for m in outcome.messages]
        assert recovered[-2].content == latest
        archive = root / outcome.details['archive']['path']
        assert 'Preserve output bytes on errors' in archive.read_text(encoding='utf-8')
        result['large_retained_request'] = {'estimated_before': before,
            'estimated_after': working.estimate_context_tokens(outcome.messages),
            'model_calls': client.calls,
            'latest_marker_copies': sum(m.content.count('UNIQUE_RETAINED_REQUEST:') for m in outcome.messages),
            'retained_request_exact': True, 'archive_recoverable': True, 'reload_matches': True}
    return result
print(json.dumps(asyncio.run(main())))
'''


def measure(source):
    env = {k: v for k, v in os.environ.items() if not k.startswith('MINICLAW_')}
    env.update(PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    p = subprocess.run([sys.executable, '-c', CHILD, str(source)], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding='utf-8', timeout=60)
    if p.returncode:
        raise RuntimeError(p.stderr)
    return json.loads(p.stdout)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--revision', type=int, default=2)
    args = p.parse_args()
    run = ROOT / f'.aster/evals/memory-cost-v{args.revision}'
    result = {'kind': 'synthetic_offline_mechanism_measurement',
        'policy': 'Fixed inputs and summary reply; character estimates, not end-to-end provider savings.',
        'baseline': measure(ROOT / '.aster/evals/hard-campaign-v1/snapshot/src'),
        'candidate': measure(run / 'snapshot/src')}
    (run / 'mechanism-comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
