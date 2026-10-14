"""Tests for the standalone ``codeboarding-expand`` CLI."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from codeboarding_cli import expand


STORED = {
    "metadata": {"generated_at": "t", "repo_name": "r", "depth_level": 1, "depth_cap": 1, "format_version": 3},
    "description": "d",
    "files": {"a.py": {"content_hash": "h", "module_hash": ""}},
    "methods_index": {
        "a.py|a.one": {"id": "AAAAAAAAAA", "start_line": 1, "end_line": 6, "type": "FUNCTION", "content_hash": ""}
    },
    "components": [
        {
            "name": "c1",
            "component_id": "1",
            "description": "d",
            "key_entities": [],
            "file_methods": ["AAAAAAAAAA"],
            "can_expand": False,
            "components": [],
            "components_relations": [],
        }
    ],
    "components_relations": [],
}


def run(*argv: str) -> None:
    parser = expand.build_parser()
    expand.run_from_args(parser.parse_args(list(argv)), parser)


class TestExpandCli(unittest.TestCase):
    def test_writes_the_expanded_document(self):
        with TemporaryDirectory() as tmp:
            stored = Path(tmp) / "analysis.json"
            stored.write_text(json.dumps(STORED), encoding="utf-8")
            out = Path(tmp) / "expanded.json"

            run(str(stored), "--output", str(out))
            expanded = json.loads(out.read_text(encoding="utf-8"))

        self.assertEqual(expanded["methods_index"]["a.py|a.one"]["qualified_name"], "a.one")
        self.assertNotIn("id", expanded["methods_index"]["a.py|a.one"])
        self.assertEqual(expanded["files"]["a.py"]["method_keys"], ["a.py|a.one"])
        self.assertEqual(expanded["components"][0]["file_methods"], [{"file_path": "a.py", "methods": ["a.one"]}])

    def test_a_missing_file_exits_with_an_error(self):
        with self.assertRaises(SystemExit):
            run("/nonexistent/analysis.json")

    def test_a_malformed_method_key_exits_with_an_error(self):
        with TemporaryDirectory() as tmp:
            stored = Path(tmp) / "analysis.json"
            stored.write_text(json.dumps({"methods_index": {"no-separator": {"start_line": 1}}}), encoding="utf-8")

            with self.assertRaises(SystemExit):
                run(str(stored))


if __name__ == "__main__":
    unittest.main()
