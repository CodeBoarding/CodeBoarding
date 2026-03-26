import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from static_analyzer.scanner import ProjectScanner


class TestProjectScanner(unittest.TestCase):
    def setUp(self):
        self.scanner = ProjectScanner(Path("/fake/repo"))

    @patch("static_analyzer.scanner.get_config")
    @patch("static_analyzer.scanner.subprocess.run")
    def test_scan_raises_on_empty_stdout(self, mock_run, mock_get_config):
        mock_get_config.return_value = {"tokei": {"command": ["tokei", "-o", "json"]}}
        mock_run.return_value = MagicMock(stdout="", stderr="some warning")

        with self.assertRaises(RuntimeError) as ctx:
            self.scanner.scan()

        self.assertIn("Tokei produced no output", str(ctx.exception))
        self.assertIn("some warning", str(ctx.exception))

    @patch("static_analyzer.scanner.get_config")
    @patch("static_analyzer.scanner.subprocess.run")
    def test_scan_raises_on_none_stdout(self, mock_run, mock_get_config):
        mock_get_config.return_value = {"tokei": {"command": ["tokei", "-o", "json"]}}
        mock_run.return_value = MagicMock(stdout=None, stderr="")

        with self.assertRaises(RuntimeError) as ctx:
            self.scanner.scan()

        self.assertIn("Tokei produced no output", str(ctx.exception))

    @patch("static_analyzer.scanner.get_config")
    @patch("static_analyzer.scanner.subprocess.run")
    def test_scan_succeeds_with_valid_output(self, mock_run, mock_get_config):
        mock_get_config.side_effect = [
            {"tokei": {"command": ["tokei", "-o", "json"]}},
            {"python": {"command": ["pyright-langserver", "--stdio"]}},
        ]
        mock_run.return_value = MagicMock(
            stdout='{"Python": {"code": 100, "reports": [{"name": "main.py"}]}, "Total": {"code": 100}}'
        )

        result = self.scanner.scan()

        self.assertIsInstance(result, list)


if __name__ == "__main__":
    unittest.main()
