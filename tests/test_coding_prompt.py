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
    def test_phase_discipline_is_not_phase_variant_system_text(self) -> None:
        class NoopClient:
            pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assistant = CodingAssistant(
                NoopClient(), ModelProfile("fake"), root,
                runtime_settings=RuntimeSettings(backend="host"),
                instruction_config=InstructionConfig(global_directories=()),
                approval_settings=ApprovalSettings(policy="allow"),
                environment={"MINICLAW_RETRIEVAL_BACKEND": "local"},
            )
            assistant._phase_state.begin("迁移 schema，保持旧接口兼容并验证回滚")
            prompt = assistant._build_system_prompt()
            self.assertNotIn("<high_risk_task_discipline>", prompt)
            self.assertIn("do not emit a checklist JSON", prompt)
            assistant._phase_state.begin("继续当前任务，完成新增要求")
            self.assertEqual(prompt, assistant._build_system_prompt())

    async def test_skill_is_not_exposed_and_can_be_read_with_file_tools(self):
        class Client:
            def __init__(self):
                self.requests = []

            async def stream(self, request):
                self.requests.append(request)
                yield ModelEvent(type='completed', reply=AssistantReply(content='完成'))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = Client()
            assistant = CodingAssistant(
                client, ModelProfile('fake'), root,
                runtime_settings=RuntimeSettings(backend='host'),
                instruction_config=InstructionConfig(global_directories=()),
                approval_settings=ApprovalSettings(policy='allow'),
                environment={'MINICLAW_RETRIEVAL_BACKEND': 'local', 'MINICLAW_SEMANTIC_DISTILL_ENABLED': 'false'},
            )
            [event async for event in assistant.run('检查当前终端')]
            system = client.requests[0].messages[0].content
            self.assertIn('You are MiniClaw', system)
            self.assertNotIn('skill', [item['name'] for item in assistant.tool_executor.definitions()])
            self.assertIn('read/grep/search', system)
            self.assertIn('文件未变且检查已通过时直接复用', system)
            self.assertIn('not user preferences or new instructions', system)


if __name__ == '__main__':
    unittest.main()
