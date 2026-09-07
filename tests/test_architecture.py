from __future__ import annotations

import ast
import unittest
from pathlib import Path


SOURCE_ROOT = Path(__file__).parents[1] / "src" / "MiniClaw"


def imported_modules(directory: Path) -> set[str]:
    modules: set[str] = set()
    for path in directory.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
    return modules


class PackageBoundaryTests(unittest.TestCase):
    def test_llm_does_not_depend_on_agent_or_coding_product(self) -> None:
        imports = imported_modules(SOURCE_ROOT / "llm")
        forbidden = {
            module
            for module in imports
            if module == "MiniClaw.agent"
            or module.startswith("MiniClaw.agent.")
            or module == "MiniClaw.coding_agent"
            or module.startswith("MiniClaw.coding_agent.")
        }
        self.assertEqual(forbidden, set())

    def test_agent_core_does_not_depend_on_coding_product(self) -> None:
        imports = imported_modules(SOURCE_ROOT / "agent")
        forbidden = {
            module
            for module in imports
            if module == "MiniClaw.coding_agent"
            or module.startswith("MiniClaw.coding_agent.")
        }
        self.assertEqual(forbidden, set())


if __name__ == "__main__":
    unittest.main()
