import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.scanner import ProjectScanner


class TestProjectScanner(unittest.TestCase):
    def setUp(self):
        repo = Path("/fake/repo")
        self.scanner = ProjectScanner(repo, RepoIgnoreManager(repo))

    @patch("static_analyzer.scanner.platform.platform", return_value="Linux-6.8.0")
    @patch("static_analyzer.scanner.is_wsl", return_value=False)
    @patch("static_analyzer.scanner.get_config")
    @patch("static_analyzer.scanner.subprocess.run")
    def test_scan_raises_on_empty_stdout(self, mock_run, mock_get_config, mock_is_wsl, mock_platform):
        mock_get_config.return_value = {"tokei": {"command": ["tokei", "-o", "json"]}}
        mock_run.return_value = MagicMock(stdout="", stderr="some warning")

        with self.assertRaises(RuntimeError) as ctx:
            self.scanner.scan()

        message = str(ctx.exception)
        self.assertIn("Tokei produced no output", message)
        self.assertIn("Platform: Linux-6.8.0", message)
        self.assertIn("WSL detected: no", message)
        self.assertIn("Command: tokei -o json", message)
        self.assertIn("some warning", message)
        self.assertIn("Verify that 'tokei -o json' works in your terminal", message)
        self.assertNotIn("Windows tokei binary", message)

    @patch("static_analyzer.scanner.platform.platform", return_value="Linux-5.15.0-microsoft")
    @patch("static_analyzer.scanner.is_wsl", return_value=True)
    @patch("static_analyzer.scanner.get_config")
    @patch("static_analyzer.scanner.subprocess.run")
    def test_scan_raises_on_none_stdout(self, mock_run, mock_get_config, mock_is_wsl, mock_platform):
        mock_get_config.return_value = {"tokei": {"command": ["tokei", "-o", "json"]}}
        mock_run.return_value = MagicMock(stdout=None, stderr="")

        with self.assertRaises(RuntimeError) as ctx:
            self.scanner.scan()

        message = str(ctx.exception)
        self.assertIn("Tokei produced no output", message)
        self.assertIn("Platform: Linux-5.15.0-microsoft", message)
        self.assertIn("WSL detected: yes", message)
        self.assertIn("Windows tokei binary", message)

    @patch("static_analyzer.scanner.platform.platform", return_value="Windows-10")
    @patch("static_analyzer.scanner.is_wsl", return_value=False)
    @patch("static_analyzer.scanner.get_config")
    @patch("static_analyzer.scanner.subprocess.run")
    def test_scan_wraps_nonzero_tokei_exit(self, mock_run, mock_get_config, mock_is_wsl, mock_platform):
        mock_get_config.return_value = {"tokei": {"command": ["tokei", "-o", "json"]}}
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=2,
            cmd=["tokei", "-o", "json"],
            stderr="failed to read repository",
        )

        with self.assertRaises(RuntimeError) as ctx:
            self.scanner.scan()

        message = str(ctx.exception)
        self.assertIn("Tokei command failed with exit code 2", message)
        self.assertIn("Platform: Windows-10", message)
        self.assertIn("WSL detected: no", message)
        self.assertIn("Command: tokei -o json", message)
        self.assertIn("failed to read repository", message)

    @patch("static_analyzer.scanner.platform.platform", return_value="Darwin-25.0.0")
    @patch("static_analyzer.scanner.is_wsl", return_value=False)
    @patch("static_analyzer.scanner.get_config")
    @patch("static_analyzer.scanner.subprocess.run")
    def test_scan_wraps_missing_tokei_binary(self, mock_run, mock_get_config, mock_is_wsl, mock_platform):
        mock_get_config.return_value = {"tokei": {"command": ["tokei", "-o", "json"]}}
        mock_run.side_effect = FileNotFoundError("No such file or directory: 'tokei'")

        with self.assertRaises(RuntimeError) as ctx:
            self.scanner.scan()

        message = str(ctx.exception)
        self.assertIn("Tokei executable not found", message)
        self.assertIn("Platform: Darwin-25.0.0", message)
        self.assertIn("Command: tokei -o json", message)
        self.assertIn("stderr: no stderr output", message)
        self.assertIn("Install tokei and ensure it is available on PATH", message)

    @patch("static_analyzer.scanner.get_config")
    @patch("static_analyzer.scanner.subprocess.run")
    def test_scan_succeeds_with_valid_output(self, mock_run, mock_get_config):
        mock_get_config.side_effect = [
            {"tokei": {"command": ["tokei", "-o", "json"]}},
            {"python": {"command": ["pyright-langserver", "--stdio"], "file_extensions": [".py"]}},
        ]
        mock_run.return_value = MagicMock(
            stdout='{"Python": {"code": 100, "reports": [{"name": "main.py"}]}, "Total": {"code": 100}}'
        )

        result = self.scanner.scan()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].language, "Python")
        self.assertEqual(result[0].size, 100)
        self.assertEqual(result[0].suffixes, [".py"])
        self.assertEqual(self.scanner.all_text_files, ["main.py"])

    @patch("static_analyzer.scanner.track_tech_stack")
    @patch("static_analyzer.scanner.get_config")
    @patch("static_analyzer.scanner.subprocess.run")
    def test_scan_ignores_languages_with_only_ignored_files(self, mock_run, mock_get_config, mock_track):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        repo = Path(temp_dir.name)
        (repo / ".codeboarding").mkdir()
        (repo / ".codeboarding" / ".codeboardingignore").write_text("tests/\n")

        scanner = ProjectScanner(repo, RepoIgnoreManager(repo))
        mock_get_config.side_effect = [
            {"tokei": {"command": ["tokei", "-o", "json"]}},
            {
                "python": {"command": ["pyright-langserver", "--stdio"], "file_extensions": [".py"]},
                "csharp": {"command": ["csharp-ls"], "file_extensions": [".cs"]},
            },
        ]
        mock_run.return_value = MagicMock(
            stdout=(
                '{"Python": {"code": 12, "reports": [{"name": "main.py", "stats": {"code": 12}}]}, '
                '"C#": {"code": 7, "reports": [{"name": "tests/Ignored.cs", "stats": {"code": 7}}]}, '
                '"Total": {"code": 19}}'
            )
        )

        result = scanner.scan()

        self.assertEqual([lang.language for lang in result], ["Python"])
        self.assertEqual(result[0].size, 12)
        self.assertEqual(result[0].percentage, 100)
        self.assertEqual(scanner.all_text_files, ["main.py"])
        mock_track.assert_called_once()
        self.assertEqual(mock_track.call_args.args[1], 12)


if __name__ == "__main__":
    unittest.main()
