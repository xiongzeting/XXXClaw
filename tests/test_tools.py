from __future__ import annotations

import codecs
import asyncio
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from MiniClaw.llm.types import ToolInvocation
from MiniClaw.coding_agent.tools import (
    BashTool,
    EditTool,
    SearchTool,
    GrepTool,
    ReadTool,
    ToolExecutor,
    ToolManager,
    ToolRolePolicy,
    ToolResult,
    WorkspaceGuard,
    WriteTool,
)
from MiniClaw.coding_agent.tools.factory import create_coding_tools


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_workspace_rejects_parent_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            boundary = WorkspaceGuard(directory)
            with self.assertRaises(PermissionError):
                boundary.resolve("../outside.txt")

    async def test_factory_registers_default_tool_names_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                [tool.name for tool in create_coding_tools(directory)],
                ["read", "bash", "edit", "write", "grep", "search"],
            )

    async def test_search_lists_files_without_exposing_protected_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('ok')", encoding="utf-8")
            (root / "top.py").write_text("", encoding="utf-8")
            (root / ".env").write_text("SECRET=value", encoding="utf-8")
            manager = ToolManager()
            manager.register(SearchTool(WorkspaceGuard(root)))
            self.assertEqual([tool["name"] for tool in manager.definitions()], ["search"])
            result = await manager.execute(
                ToolInvocation("search-1", "search", {"pattern": "**/*"})
            )
            self.assertFalse(result.is_error)
            self.assertIn("src/app.py", result.content)
            self.assertIn("top.py", result.content)
            self.assertNotIn(".env", result.content)

    async def test_search_manager_preserves_path_filter_and_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            for relative in ("src/a.py", "src/b.py", "src/notes.txt", "top.py"):
                (root / relative).write_text("", encoding="utf-8")
            manager = ToolManager()
            for tool in create_coding_tools(directory):
                manager.register(tool)

            scoped = await manager.execute(
                ToolInvocation("search-1", "search", {"pattern": "**/*.py", "path": "src"})
            )
            self.assertFalse(scoped.is_error)
            self.assertEqual(scoped.details["matchedPaths"], ["src/a.py", "src/b.py"])
            self.assertFalse(scoped.details["truncated"])

            limited = await manager.execute(
                ToolInvocation(
                    "search-2", "search", {"pattern": "**/*.py", "path": "src", "limit": 1}
                )
            )
            self.assertFalse(limited.is_error)
            self.assertEqual(limited.details["matchedPaths"], ["src/a.py"])
            self.assertEqual(limited.details["matches"], 1)
            self.assertTrue(limited.details["truncated"])

            invalid = await manager.execute(
                ToolInvocation("search-3", "search", {"pattern": "**/*.py", "limit": 0})
            )
            self.assertTrue(invalid.is_error)
            self.assertIn("arguments.limit must be at least 1", invalid.content)

    async def test_tool_manager_supports_role_injection_and_policy(self) -> None:
        class NamedTool:
            description = "test"
            input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

            def __init__(self, name: str) -> None:
                self.name = name

            async def execute(self, arguments):
                return ToolResult(content=self.name)

        manager = ToolManager(
            active_role="reviewer",
            role_policies={"reviewer": ToolRolePolicy(deny=frozenset({"write"}))},
        )
        manager.register(NamedTool("read"))
        manager.register(NamedTool("write"))
        manager.inject("reviewer", [NamedTool("review_notes")])
        manager.inject("operator", [NamedTool("deploy")])

        self.assertEqual(manager.available_names(), ("read", "review_notes"))
        denied = await manager.execute(ToolInvocation("1", "write", {}))
        hidden = await manager.execute(ToolInvocation("2", "deploy", {}))
        self.assertTrue(denied.is_error)
        self.assertTrue(hidden.is_error)
        manager.set_role("operator")
        self.assertEqual(manager.available_names(), ("read", "write", "deploy"))

    async def test_tool_manager_rejects_malformed_role_policy(self) -> None:
        with self.assertRaisesRegex(TypeError, "iterable of tool names"):
            ToolManager(role_policies={"reviewer": {"allow": "read"}})
        with self.assertRaisesRegex(ValueError, "unknown tool role policy fields"):
            ToolManager(role_policies={"reviewer": {"allows": ["read"]}})
        with self.assertRaisesRegex(ValueError, "role must be non-empty"):
            ToolManager(role_policies={" ": {"allow": ["read"]}})

    async def test_write_creates_parents_and_read_supports_offset_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executor = ToolExecutor()
            executor.register(WriteTool(WorkspaceGuard(directory)))
            executor.register(ReadTool(WorkspaceGuard(directory)))
            written = await executor.execute(
                ToolInvocation("1", "write", {"path": "nested/a.txt", "content": "one\ntwo\nthree\nfour"})
            )
            result = await executor.execute(
                ToolInvocation("2", "read", {"path": "nested/a.txt", "offset": 2, "limit": 2})
            )
            self.assertFalse(written.is_error)
            self.assertEqual((Path(directory) / "nested" / "a.txt").read_text(encoding="utf-8"), "one\ntwo\nthree\nfour")
            self.assertTrue(result.content.startswith("two\nthree"))
            self.assertIn("offset=4", result.content)

    async def test_read_rejects_offset_beyond_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "a.txt").write_text("one\ntwo", encoding="utf-8")
            executor = ToolExecutor()
            executor.register(ReadTool(WorkspaceGuard(directory)))
            result = await executor.execute(ToolInvocation("1", "read", {"path": "a.txt", "offset": 3}))
            self.assertTrue(result.is_error)
            self.assertIn("beyond end of file", result.content)

    async def test_read_truncates_head_and_provides_next_offset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "large.txt").write_text("\n".join(str(i) for i in range(2_101)), encoding="utf-8")
            result = await ReadTool(WorkspaceGuard(directory)).execute({"path": "large.txt"})
            self.assertIn("Use offset=2001 to continue", result.content)
            self.assertEqual(result.details["truncation"]["truncated_by"], "lines")

    async def test_read_does_not_return_partial_oversized_first_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "wide.txt").write_text("汉" * 20_000, encoding="utf-8")
            result = await ReadTool(WorkspaceGuard(directory)).execute({"path": "wide.txt"})
            self.assertTrue(result.content.startswith("[Line 1 is"))
            self.assertIn("Use bash", result.content)

    async def test_edit_applies_multiple_original_file_replacements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "a.txt")
            path.write_text("alpha beta gamma", encoding="utf-8")
            result = await EditTool(WorkspaceGuard(directory)).execute(
                {
                    "path": "a.txt",
                    "edits": [
                        {"oldText": "alpha", "newText": "A"},
                        {"oldText": "gamma", "newText": "G"},
                    ],
                }
            )
            self.assertEqual(path.read_text(encoding="utf-8"), "A beta G")
            self.assertEqual(result.details["firstChangedLine"], 1)
            self.assertIn("-1 alpha beta gamma", result.details["diff"])

    async def test_edit_rejects_duplicate_and_overlap_without_writing(self) -> None:
        cases = [
            ("x x", [{"oldText": "x", "newText": "y"}], "occurrences"),
            (
                "abcde",
                [{"oldText": "abc", "newText": "A"}, {"oldText": "bcd", "newText": "B"}],
                "overlap",
            ),
        ]
        for original, edits, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                path = Path(directory, "a.txt")
                path.write_text(original, encoding="utf-8")
                executor = ToolExecutor()
                executor.register(EditTool(WorkspaceGuard(directory)))
                result = await executor.execute(ToolInvocation("1", "edit", {"path": "a.txt", "edits": edits}))
                self.assertTrue(result.is_error)
                self.assertIn(message, result.content)
                self.assertEqual(path.read_text(encoding="utf-8"), original)

    async def test_edit_preserves_crlf_and_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "a.txt")
            path.write_bytes(codecs.BOM_UTF8 + b"one\r\ntwo\r\n")
            await EditTool(WorkspaceGuard(directory)).execute(
                {"path": "a.txt", "edits": [{"oldText": "two", "newText": "second"}]}
            )
            self.assertEqual(path.read_bytes(), codecs.BOM_UTF8 + b"one\r\nsecond\r\n")

    async def test_edit_accepts_legacy_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "a.txt")
            path.write_text("old", encoding="utf-8")
            executor = ToolExecutor()
            executor.register(EditTool(WorkspaceGuard(directory)))
            result = await executor.execute(
                ToolInvocation("1", "edit", {"path": "a.txt", "oldText": "old", "newText": "new"})
            )
            self.assertFalse(result.is_error)
            self.assertEqual(path.read_text(encoding="utf-8"), "new")

            stringified = await executor.execute(
                ToolInvocation(
                    "2",
                    "edit",
                    {"path": "a.txt", "edits": '[{"oldText":"new","newText":"final"}]'},
                )
            )
            self.assertFalse(stringified.is_error)
            self.assertEqual(path.read_text(encoding="utf-8"), "final")

    @unittest.skipUnless(shutil.which("rg"), "ripgrep is required")
    async def test_grep_supports_regex_literal_glob_case_and_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "a.py").write_text("before\nHello 123\nafter\n", encoding="utf-8")
            Path(directory, "b.txt").write_text("hello 999\n", encoding="utf-8")
            tool = GrepTool(WorkspaceGuard(directory))
            regex = await tool.execute(
                {"pattern": "hello \\d+", "glob": "*.py", "ignoreCase": True, "context": 1}
            )
            literal = await tool.execute({"pattern": "Hello 123", "literal": True})
            self.assertIn("a.py:2: Hello 123", regex.content)
            self.assertIn("a.py-1- before", regex.content)
            self.assertNotIn("b.txt", regex.content)
            self.assertIn("a.py:2: Hello 123", literal.content)

    @unittest.skipUnless(shutil.which("rg"), "ripgrep is required")
    async def test_grep_limit_and_long_line_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "a.txt").write_text(("match " + "x" * 600 + "\n") * 3, encoding="utf-8")
            result = await GrepTool(WorkspaceGuard(directory)).execute({"pattern": "match", "limit": 2})
            self.assertIn("2 matches limit reached", result.content)
            self.assertIn("[truncated]", result.content)
            self.assertEqual(result.details["matchLimitReached"], 2)

    @unittest.skipUnless(shutil.which("rg"), "ripgrep is required")
    async def test_grep_large_match_stream_stops_without_pipe_deadlock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "many.txt").write_text(
                "".join(f"match {index:05d} {'x' * 256}\n" for index in range(20_000)),
                encoding="utf-8",
            )
            result = await asyncio.wait_for(
                GrepTool(WorkspaceGuard(directory)).execute({"pattern": "match", "limit": 200}),
                timeout=10,
            )

            self.assertEqual(result.details["matches"], 200)
            self.assertEqual(result.details["matchLimitReached"], 200)

    async def test_bash_success_nonzero_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tool = BashTool(WorkspaceGuard(directory))
            executable = subprocess.list2cmdline([sys.executable])
            success = await tool.execute({"command": f'{executable} -c "print(123)"'})
            failure = await tool.execute({"command": f'{executable} -c "import sys; print(\'bad\'); sys.exit(3)"'})
            self.assertEqual(success.content.strip(), "123")
            self.assertTrue(failure.is_error)
            self.assertIn("Command exited with code 3", failure.content)
            with self.assertRaises(TimeoutError):
                await tool.execute(
                    {"command": f'{executable} -c "import time; time.sleep(2)"', "timeout": 0.1}
                )

            executor = ToolExecutor()
            executor.register(tool)
            timed_out = await executor.execute(
                ToolInvocation(
                    "1",
                    "bash",
                    {"command": f'{executable} -c "import time; time.sleep(2)"', "timeout": 0.1},
                )
            )
            self.assertTrue(timed_out.is_error)
            self.assertIn("Command timed out", timed_out.content)

    async def test_bash_details_separate_streams_and_workspace_change_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = BashTool(WorkspaceGuard(root))
            executable = subprocess.list2cmdline([sys.executable])
            command = f'{executable} -c "import pathlib,sys; pathlib.Path(\'changed.txt\').write_text(\'ok\'); pathlib.Path(\'.aster\').mkdir(exist_ok=True); pathlib.Path(\'.aster/internal\').write_text(\'ignored\'); print(\'out\'); print(\'err\', file=sys.stderr)"'
            result = await tool.execute({"command": command})
            self.assertEqual(result.details["stdout"].strip(), "out")
            self.assertEqual(result.details["stderr"].strip(), "err")
            changes = result.details["workspace_changes"]
            self.assertTrue(changes["complete"])
            self.assertEqual(changes["changed_paths"], ["changed.txt"])

    async def test_bash_keeps_tail_and_saves_full_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tool = BashTool(WorkspaceGuard(directory))
            executable = subprocess.list2cmdline([sys.executable])
            command = f'{executable} -c "print(\'\\n\'.join(str(i) for i in range(2105)))"'
            result = await tool.execute({"command": command})
            self.assertNotIn("\n0\n", f"\n{result.content}\n")
            self.assertIn("2104", result.content)
            output_path = Path(directory, result.details["fullOutputPath"])
            self.assertTrue(output_path.is_file())
            self.assertIn("0\n1\n", output_path.read_text(encoding="utf-8"))

    async def test_recursive_schema_validation_rejects_bad_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "a.txt").write_text("x", encoding="utf-8")
            executor = ToolExecutor()
            executor.register(EditTool(WorkspaceGuard(directory)))
            result = await executor.execute(
                ToolInvocation("1", "edit", {"path": "a.txt", "edits": [{"oldText": "x"}]})
            )
            self.assertTrue(result.is_error)
            self.assertIn("newText", result.content)


if __name__ == "__main__":
    unittest.main()
