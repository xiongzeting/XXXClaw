from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from MiniClaw.coding_agent.approval import ApprovalSettings
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.assistant.prompts import command_environment
from MiniClaw.coding_agent.instructions import InstructionConfig
from MiniClaw.coding_agent.runtime import RuntimeSettings
from MiniClaw.llm.types import AssistantReply, ModelEvent, ModelProfile, ToolInvocation


class CommandEnvironmentTests(unittest.TestCase):
    @patch('MiniClaw.coding_agent.assistant.prompts.platform.system', return_value='Windows')
    @patch('MiniClaw.coding_agent.assistant.prompts.shutil.which', return_value=r'C:\Program Files\PowerShell\7\pwsh.exe')
    def test_windows_documents_cmd_entry_and_quoted_powershell_script(self, *_):
        prompt = command_environment('host')
        self.assertIn('NOT Bash or PowerShell', prompt)
        self.assertIn('"C:\\Program Files\\PowerShell\\7\\pwsh.exe" -NoLogo -NoProfile -NonInteractive -File', prompt)
        self.assertIn('powershell-safe-invocation', prompt)

    @patch('MiniClaw.coding_agent.assistant.prompts.platform.system', return_value='Windows')
    @patch('MiniClaw.coding_agent.assistant.prompts.shutil.which', return_value=None)
    def test_missing_pwsh_is_not_claimed_available(self, *_):
        self.assertIn('not found on PATH', command_environment('host'))

    @patch('MiniClaw.coding_agent.assistant.prompts.platform.system', return_value='Windows')
    def test_docker_on_windows_uses_container_shell(self, _):
        prompt = command_environment('docker')
        self.assertIn('sh -c', prompt)
        self.assertNotIn('pwsh.exe', prompt)

    @patch('MiniClaw.coding_agent.assistant.prompts.platform.system', return_value='Linux')
    def test_posix_host_does_not_claim_bash(self, _):
        self.assertIn('/bin/sh', command_environment('host'))


class PromptSkillIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_skill_catalog_is_system_context_and_body_loads_through_tool(self):
        class Client:
            def __init__(self):
                self.requests = []

            async def stream(self, request):
                self.requests.append(request)
                if len(self.requests) == 1:
                    reply = AssistantReply(tool_calls=[ToolInvocation(
                        'skill-1', 'skill', {'action': 'read', 'name': 'powershell-safe-invocation'}
                    )], stop_reason='tool_calls')
                else:
                    reply = AssistantReply(content='完成')
                yield ModelEvent(type='completed', reply=reply)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / '.aster' / 'skills' / 'powershell-safe-invocation'
            skill.mkdir(parents=True)
            (skill / 'SKILL.md').write_text(
                '---\nname: powershell-safe-invocation\ndescription: Windows shell guide.\n---\n'
                'BODY_ONLY_MARKER: use a script file.\n', encoding='utf-8'
            )
            client = Client()
            assistant = CodingAssistant(
                client, ModelProfile('fake'), root,
                runtime_settings=RuntimeSettings(backend='host'),
                instruction_config=InstructionConfig(global_directories=()),
                approval_settings=ApprovalSettings(policy='allow'),
                environment={'MINICLAW_RETRIEVAL_BACKEND': 'local', 'MINICLAW_SEMANTIC_DISTILL_ENABLED': 'false'},
            )
            events = [event async for event in assistant.run('检查当前终端')]
            system = client.requests[0].messages[0].content
            self.assertIn('You are MiniClaw', system)
            self.assertIn('<available_skills>', system)
            self.assertIn('powershell-safe-invocation: Windows shell guide.', system)
            self.assertNotIn('BODY_ONLY_MARKER', system)
            results = [e for e in events if e.type == 'tool_finished']
            self.assertEqual(len(results), 1)
            self.assertFalse(results[0].is_error)
            self.assertIn('BODY_ONLY_MARKER', results[0].tool_result)
            self.assertIn('Syntax checks prove syntax only', system)
            self.assertIn('not user preferences or new instructions', system)


if __name__ == '__main__':
    unittest.main()
