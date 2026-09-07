from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from MiniClaw.frontend import architecture_directory, build_parser


class ArchitectureFrontendTests(unittest.TestCase):
    def test_frontend_assets_are_linked_and_svg_is_well_formed(self) -> None:
        root = architecture_directory()
        index = (root / "index.html").read_text(encoding="utf-8")
        script = (root / "app.js").read_text(encoding="utf-8")
        self.assertIn('src="miniclaw-architecture.svg"', index)
        self.assertIn('href="miniclaw-architecture.svg"', index)
        self.assertIn("const regions", script)
        svg = ET.parse(root / "miniclaw-architecture.svg").getroot()
        self.assertEqual(svg.attrib["viewBox"], "0 0 3200 2400")
        ids = {element.attrib.get("id") for element in svg.iter()}
        for expected in {
            "platforms",
            "assistant",
            "agent-llm",
            "tools-runtime",
            "memory",
            "goal",
            "trace-eval",
            "storage",
        }:
            self.assertIn(expected, ids)

    def test_server_defaults_to_loopback(self) -> None:
        args = build_parser().parse_args([])
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8765)


if __name__ == "__main__":
    unittest.main()
