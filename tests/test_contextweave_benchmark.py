from __future__ import annotations

import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

CONTEXTWEAVE_ROOT = Path(__file__).resolve().parents[1] / "external" / "benchmarks" / "ContextWeave-main"
if str(CONTEXTWEAVE_ROOT) not in sys.path:
    sys.path.insert(0, str(CONTEXTWEAVE_ROOT))

from MiniClaw.benchmark.contextweave import (
    concatenate_memory_context,
    prepare_memory_store,
    retrieve_memory_context,
)
class ContextWeaveBenchmarkTests(unittest.TestCase):
    def test_codex_home_escapes_windows_trusted_paths(self) -> None:
        from contextweave.utils.codex_home import prepare_codex_home

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_codex_home(
                root / "codex-home",
                base_url="https://example.test/v1",
                api_key="test-key",
                model="test-model",
                reasoning_effort="medium",
                trusted_dirs=[Path(r"D:\workspace\project")],
            )
            with (root / "codex-home" / "config.toml").open("rb") as handle:
                parsed = tomllib.load(handle)

            self.assertEqual(parsed["projects"][r"D:\workspace\project"]["trust_level"], "trusted")

    def test_ignores_macos_appledouble_context_files(self) -> None:
        from contextweave.runner.task_data import context_files_for_subtask

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context_dir = root / "_resources" / "subtask_0001" / "files" / "context"
            context_dir.mkdir(parents=True)
            (context_dir / "notes.json").write_text('{"ok": true}', encoding="utf-8")
            (context_dir / "._notes.json").write_bytes(b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X")

            self.assertEqual(
                context_files_for_subtask(root, "subtask_0001"),
                [context_dir / "notes.json"],
            )

    def test_builds_shared_production_memory_corpus_for_both_arms(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "sessions.json"
            dataset.write_text(
                json.dumps(
                    {
                        "sessions": [
                            {
                                "session_id": "subtask_0001",
                                "messages": [
                                    {
                                        "role": "user",
                                        "content": "Remember [project]: parser verification uses pytest. First inspect the parser, then run the focused test.",
                                    },
                                    {
                                        "role": "assistant",
                                        "content": "Implemented the parser fix and verified it with pytest tests/test_parser.py.",
                                    },
                                ],
                            },
                            {
                                "session_id": "subtask_0002",
                                "messages": [
                                    {"role": "user", "content": "Current task that must not be injected."},
                                    {"role": "assistant", "content": "Current outcome."},
                                ],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            store = root / "store"
            skill = store / ".aster" / "skills" / "parser-verification" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: parser-verification\ndescription: Verify parser changes\n---\n\n"
                "Inspect the parser and run its focused pytest test.",
                encoding="utf-8",
            )
            environment = {
                "MINICLAW_MEMORY_VECTOR_ENABLED": "false",
                "MINICLAW_MEMORY_CROSS_ENCODER_ENABLED": "false",
            }
            manifest = prepare_memory_store(dataset, 2, store, environment)

            self.assertEqual(manifest["counts"]["sessions"], 1)
            self.assertGreater(manifest["counts"]["archive_chunks"], 0)
            self.assertEqual(manifest["counts"]["episodes"], 1)
            self.assertEqual(manifest["counts"]["semantic"], 1)
            self.assertEqual(manifest["counts"]["procedural"], 0)

            retrieved, diagnostics = retrieve_memory_context(
                store,
                "How should I verify the parser change?",
                environment,
            )
            concatenated, concat_diagnostics = concatenate_memory_context(store)

            self.assertIn("pytest", retrieved)
            self.assertIn("semantic_memory", concatenated)
            self.assertIn("episodic_memory", concatenated)
            self.assertIn("procedural_memory", concatenated)
            self.assertIn("working_archive", concatenated)
            self.assertEqual(diagnostics["mode"], "retrieval")
            self.assertEqual(concat_diagnostics["mode"], "concat")
            self.assertEqual(concat_diagnostics["sessions"], 1)


if __name__ == "__main__":
    unittest.main()
