import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from MiniClaw.coding_agent.tools.grep import GrepTool
from MiniClaw.coding_agent.tools.workspace import WorkspaceGuard


@unittest.skipUnless(shutil.which('rg'), 'ripgrep is required')
class GrepDistributionTests(unittest.IsolatedAsyncioTestCase):
    async def test_roots_and_globs_agree_with_ripgrep_reference(self):
        with tempfile.TemporaryDirectory(prefix='grep distribution ') as directory:
            root = Path(directory).resolve()
            for path in ['root.txt', 'src/a.txt', 'src/deep/b.txt', 'other/a.txt', 'other/src/c.txt', '中文目录/a.txt']:
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('marker-913\n', encoding='utf-8')
            tool = GrepTool(WorkspaceGuard(root))
            for base in ['', '.', 'src']:
                for glob in ['', '*.txt', 'src/*.txt', 'src/**/*.txt', 'other/src/*.txt', '**/a.txt', 'absent/*.txt', '中文目录/*.txt']:
                    with self.subTest(base=base, glob=glob):
                        args = ['rg', '--json', '--hidden']
                        if glob:
                            args += ['--glob', glob]
                        args += ['--', 'marker-913', str(root / (base or '.'))]
                        run = subprocess.run(args, cwd=root, capture_output=True, encoding='utf-8')
                        self.assertIn(run.returncode, [0,1])
                        expected = {str(Path(row['data']['path']['text']).resolve()) for line in run.stdout.splitlines() if (row := json.loads(line))['type'] == 'match'}
                        result = await tool.execute({'pattern':'marker-913', 'path':base, 'glob':glob})
                        self.assertEqual(set(result.details['matchedPaths']), expected)

    async def test_explicit_file_does_not_scan_same_named_nested_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'sub').mkdir()
            (root / 'config.txt').write_text('needle\n')
            (root / 'sub/config.txt').write_text('needle\n')
            result = await GrepTool(WorkspaceGuard(root)).execute({'pattern':'needle','path':'config.txt'})
            self.assertEqual(result.details['matches'], 1)
