from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from MiniClaw.coding_agent.tools.search import SearchTool
from MiniClaw.coding_agent.tools.workspace import WorkspaceGuard


class SearchPathTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name in ('tower-defense/index.html', 'tower-defense/nested/index.html',
                     'other/index.html', 'src/app.py', 'src/deep/more.py', '.aster/private.txt'):
            file = self.root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('test', encoding='utf-8')
        self.tool = SearchTool(WorkspaceGuard(self.root))

    async def test_exact_path_never_walks_workspace(self):
        with patch.object(SearchTool, '_scan_tree', side_effect=AssertionError('unexpected walk')):
            for pattern, expected in (
                ('tower-defense/index.html', ['tower-defense/index.html']),
                (r'tower-defense\index.html', ['tower-defense/index.html']),
                ('tower-defense/missing.html', []),
                ('Tower-defense/index.html', []),
                ('tower-defense/INDEX.html', []),
                ('.aster/private.txt', []),
                ('../other/index.html', []),
            ):
                with self.subTest(pattern=pattern):
                    result = await self.tool.execute({'pattern': pattern})
                    self.assertEqual(result.details['matchedPaths'], expected)

    async def test_fixed_prefix_walks_only_relevant_tree(self):
        scan = SearchTool._scan_tree
        with patch.object(SearchTool, '_scan_tree', autospec=True, side_effect=scan) as spy:
            result = await self.tool.execute({'pattern': 'tower-defense/**/*.html'})
            spy.assert_called_once_with(self.tool, self.root / 'tower-defense', 'tower-defense/**/*.html', False, 1000)
        # Preserve fnmatch semantics: **/ does not match zero directories when
        # it occurs in the middle of the pattern.
        self.assertEqual(result.details['matchedPaths'], ['tower-defense/nested/index.html'])

    async def test_prefix_intersects_explicit_root(self):
        for pattern, path, expected in (
            ('src/*.py', 'src/deep', ['src/deep/more.py']),
            ('src/deep/*.py', 'src', ['src/deep/more.py']),
            ('tower-defense/index.html', 'src', []),
            ('tower-defense/*.html', 'src', []),
        ):
            with self.subTest(pattern=pattern, path=path):
                result = await self.tool.execute({'pattern': pattern, 'path': path})
                self.assertEqual(result.details['matchedPaths'], expected)

    async def test_basename_still_searches_recursively(self):
        result = await self.tool.execute({'pattern': 'index.html'})
        self.assertEqual(set(result.details['matchedPaths']), {
            'tower-defense/index.html', 'tower-defense/nested/index.html', 'other/index.html'
        })

    async def test_broad_pattern_does_not_resolve_unmatched_files(self):
        target = self.root / 'games' / 'shooting.html'
        target.parent.mkdir()
        target.write_text('game', encoding='utf-8')
        original = WorkspaceGuard.is_protected
        checked = []

        def track(guard, path, **kwargs):
            checked.append(Path(path))
            return original(guard, path, **kwargs)

        with patch.object(WorkspaceGuard, 'is_protected', autospec=True, side_effect=track):
            result = await self.tool.execute({'pattern': '**/*shoot*'})
        self.assertEqual(result.details['matchedPaths'], ['games/shooting.html'])
        self.assertIn(target, checked)
        self.assertNotIn(self.root / 'tower-defense' / 'index.html', checked)
        self.assertNotIn(self.root / 'src' / 'app.py', checked)

    async def test_broad_matches_still_filter_protected_files(self):
        public = self.root / 'public'
        public.mkdir()
        for name in ('visible.txt', '.env', 'credentials.json', 'custom-private.txt'):
            (public / name).write_text('test', encoding='utf-8')
        tool = SearchTool(WorkspaceGuard(self.root, protected_paths=[public / 'custom-private.txt']))
        result = await tool.execute({'pattern': '**/*', 'path': 'public'})
        self.assertEqual(result.details['matchedPaths'], ['public/visible.txt'])

    async def test_broad_scan_prunes_protected_subtrees_and_junctions(self):
        tool = SearchTool(WorkspaceGuard(self.root, protected_paths=[self.root / 'tower-defense']))
        # Simulate junction metadata without requiring Windows symlink privilege.
        with patch('MiniClaw.coding_agent.tools.search._is_junction', side_effect=lambda entry: entry.name == 'other'):
            result = await tool.execute({'pattern': '**/*', 'includeDirectories': True})
        self.assertEqual(result.details['matchedPaths'], ['src/', 'src/deep/', 'src/app.py', 'src/deep/more.py'])

    async def test_broad_scan_sees_new_files_without_stale_cache(self):
        first = await self.tool.execute({'pattern': '**/*shoot*'})
        self.assertEqual(first.details['matchedPaths'], [])
        (self.root / 'shoot-new.html').write_text('new', encoding='utf-8')
        second = await self.tool.execute({'pattern': '**/*shoot*'})
        self.assertEqual(second.details['matchedPaths'], ['shoot-new.html'])

    async def test_directory_root_and_limit_semantics(self):
        result = await self.tool.execute({'pattern': 'tower-defense/nested', 'includeDirectories': True})
        self.assertEqual(result.details['matchedPaths'], ['tower-defense/nested/'])
        result = await self.tool.execute({'pattern': 'tower-defense/nested', 'path': 'tower-defense/nested', 'includeDirectories': True})
        self.assertEqual(result.details['matchedPaths'], [])
        result = await self.tool.execute({'pattern': 'src/*.py', 'limit': 1})
        self.assertEqual(result.details['matchedPaths'], ['src/app.py'])
        self.assertTrue(result.details['truncated'])

    async def test_protected_prefix_and_symlink_are_not_traversed(self):
        guarded = SearchTool(WorkspaceGuard(self.root, protected_paths=[self.root / 'tower-defense']))
        for pattern in ('tower-defense/index.html', 'tower-defense/*.html'):
            result = await guarded.execute({'pattern': pattern})
            self.assertEqual(result.details['matchedPaths'], [])
        alias = self.root / 'alias'
        try:
            alias.symlink_to(self.root / 'tower-defense', target_is_directory=True)
        except OSError:
            self.skipTest('Directory symlinks unavailable')
        for pattern in ('alias/index.html', 'alias/*.html'):
            result = await self.tool.execute({'pattern': pattern})
            self.assertEqual(result.details['matchedPaths'], [])


if __name__ == '__main__':
    unittest.main()
