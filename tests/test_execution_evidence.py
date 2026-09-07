import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from MiniClaw.coding_agent.tools.bash import BashTool, _snapshot_workspace, _workspace_changes
from MiniClaw.coding_agent.tools.workspace import WorkspaceGuard
from MiniClaw.coding_agent.assistant.progress import TaskProgress
from MiniClaw.llm.types import ToolInvocation
from MiniClaw.coding_agent.runtime.capabilities import discover_execution_capabilities, _find_node_playwright


class ExecutionEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdout_verification_with_stderr_and_readonly_then_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'app.txt').write_text('expected', encoding='utf-8')
            (root / 'check.py').write_text('''import json, sys
from pathlib import Path
print("diagnostic warning", file=sys.stderr)
value = Path('app.txt').read_text()
print(json.dumps({'task_checks':[{'criterion_id':'behavior','version':1,'passed':value=='expected','evidence':value,'artifacts':['app.txt','check.py']}]}))
''', encoding='utf-8')
            progress = TaskProgress(root / '.aster', root)
            progress.checkpoint('check', [], 'verify', [{'criterion_id':'behavior','description':'correct output','version':1}])
            tool = BashTool(WorkspaceGuard(root))
            command = f'"{sys.executable}" -X utf8 check.py'
            result = await tool.execute({'command': command})
            self.assertEqual(result.details['stderr'].strip(), 'diagnostic warning')
            self.assertTrue(result.details['workspace_changes']['complete'])
            self.assertEqual(result.details['workspace_changes']['changed_paths'], [])
            progress.observe(ToolInvocation('v', 'bash', {'task_verification': True}), result)
            self.assertIsNone(progress.final_blocker())
            (root / 'mutate.py').write_text("from pathlib import Path\nPath('app.txt').write_text('bad')", encoding='utf-8')
            result = await tool.execute({'command': f'"{sys.executable}" mutate.py'})
            self.assertIn('app.txt', result.details['workspace_changes']['changed_paths'])
            progress.observe(ToolInvocation('m', 'bash', {}), result)
            self.assertIsNotNone(progress.final_blocker())

    def test_snapshot_limit_is_unknown_not_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'large').write_bytes(b'x' * 100)
            with patch('MiniClaw.coding_agent.tools.bash._SNAPSHOT_MAX_FILE_BYTES', 50):
                before = _snapshot_workspace(root)
                changes = _workspace_changes(before, _snapshot_workspace(root))
                self.assertFalse(changes['complete'])

    def test_node_package_discovery_and_container_do_not_reuse_host_capabilities(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root / 'node_modules/playwright/package.json'
            package.parent.mkdir(parents=True)
            package.write_text('{}')
            with patch.dict(os.environ, {'MINICLAW_PLAYWRIGHT_ROOTS': str(root)}):
                self.assertEqual(_find_node_playwright(), package.parent.resolve())
            value = discover_execution_capabilities(runtime='docker')
            self.assertFalse(value['environment']['edge']['available'])
            self.assertFalse(value['environment']['playwright']['checked_in_runtime'])


if __name__ == '__main__':
    unittest.main()
