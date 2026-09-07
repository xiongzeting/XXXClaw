"""Real Runtime write traces plus Docker mount verification; no model API calls."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.runtime import RuntimeSettings
from MiniClaw.evaluation.path_audit import audit_write_paths
from MiniClaw.llm.types import AssistantReply, ModelEvent, ModelProfile, ToolInvocation
from MiniClaw.trace.store import read_trace_records


class FixedClient:
    def __init__(self, calls): self.calls = calls
    async def stream(self, request):
        calls, self.calls = self.calls, []
        yield ModelEvent(type='completed', reply=AssistantReply(content='' if calls else 'Done', tool_calls=calls))


async def main():
    assert os.name == 'nt', 'This validation must exercise a Windows host'
    destination = ROOT/'.aster/evals/path-oracle-v2/docker-validation.json'
    with tempfile.TemporaryDirectory(prefix='miniclaw-path-audit-') as folder:
        workspace = Path(folder).resolve()
        paths = ['output.json', '/workspace/output.json', str(workspace/'output.json')]
        calls = [ToolInvocation(f'write-{i}', 'write', {'path': p, 'content': '{"ok":true}'}) for i,p in enumerate(paths)]
        calls.append(ToolInvocation('extra', 'write', {'path': '/workspace/extra.json', 'content': 'temporary'}))
        assistant = CodingAssistant(FixedClient(calls), ModelProfile('scripted-offline'), workspace,
            approval_handler=lambda _request: True,
            environment={}, runtime_settings=RuntimeSettings(backend='docker', docker_image='miniclaw-runtime:py313-bench'))
        events = [e async for e in assistant.run('Create the specified test artifacts.')]
        assert not any(e.type == 'error' for e in events)
        records = read_trace_records(workspace/'.aster/trace.jsonl')
        writes = [e for e in records if e['type']=='tool.call' and e['data'].get('tool_name')=='write']
        assert len(writes)==4 and all(e['data']['status']=='success' for e in writes), writes
        verification = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only',
            '--mount', f'type=bind,source={workspace},target=/workspace,readonly',
            'miniclaw-runtime:py313-bench', 'python', '-c',
            'import json; from pathlib import Path; assert json.loads(Path("/workspace/output.json").read_text())=={"ok":True}; assert Path("/workspace/extra.json").read_text()=="temporary"; print("DOCKER_PATHS_OK")'],
            capture_output=True, text=True, timeout=60)
        assert verification.returncode==0, verification.stderr
        positive = audit_write_paths(workspace, writes[:3], ['output.json'])
        assert positive['passed']
        # Remove only the known temporary artifact inside this disposable workspace.
        (workspace/'extra.json').unlink()
        negative = audit_write_paths(workspace, writes, ['output.json'])
        assert not negative['passed'] and len(negative['violations'])==1
        result = {'host': 'Windows', 'docker_image':'miniclaw-runtime:py313-bench',
                  'model_calls': 'scripted, no network API', 'docker_verification': verification.stdout.strip(),
                  'positive': positive, 'write_then_delete': negative,
                  'extra_absent_at_audit': not (workspace/'extra.json').exists()}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print('Windows + Docker: 3 legitimate path forms accepted; extra write caught after deletion')


if __name__ == '__main__': asyncio.run(main())
