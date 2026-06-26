"""Tests for Windows-sensitive path handling."""

import platform
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from static_analyzer.engine.utils import uri_to_path
from tool_registry.paths import is_wsl

IS_WINDOWS = platform.system() == "Windows"


class TestFileURIParsing(unittest.TestCase):
    def test_unix_file_uri(self):
        self.assertEqual(
            uri_to_path("file:///home/user/project/file.py"),
            Path("/home/user/project/file.py"),
        )

    def test_empty_uri(self):
        self.assertIsNone(uri_to_path(""))

    def test_non_file_scheme(self):
        self.assertIsNone(uri_to_path("http://example.com/foo"))


class TestWSLDetection(unittest.TestCase):
    @patch("tool_registry.paths.Path.read_text", return_value="Linux version 5.15.0 Microsoft")
    @patch("tool_registry.paths.platform.release", return_value="5.15.0-generic")
    @patch("tool_registry.paths.platform.system", return_value="Linux")
    def test_detects_proc_version_microsoft_marker(self, mock_system, mock_release, mock_read_text):
        self.assertTrue(is_wsl())

    @patch("tool_registry.paths.Path.read_text", side_effect=OSError("missing /proc/version"))
    @patch("tool_registry.paths.platform.release", return_value="5.15.90.1-microsoft-standard-WSL2")
    @patch("tool_registry.paths.platform.system", return_value="Linux")
    def test_detects_release_wsl_marker(self, mock_system, mock_release, mock_read_text):
        self.assertTrue(is_wsl())
        mock_read_text.assert_not_called()

    @patch("tool_registry.paths.Path.read_text", return_value="Linux version 6.8.0 generic")
    @patch("tool_registry.paths.platform.release", return_value="6.8.0-generic")
    @patch("tool_registry.paths.platform.system", return_value="Linux")
    def test_native_linux_is_not_wsl(self, mock_system, mock_release, mock_read_text):
        self.assertFalse(is_wsl())

    @patch("tool_registry.paths.Path.read_text")
    @patch("tool_registry.paths.platform.release", return_value="23.0.0")
    @patch("tool_registry.paths.platform.system", return_value="Darwin")
    def test_non_linux_is_not_wsl(self, mock_system, mock_release, mock_read_text):
        self.assertFalse(is_wsl())
        mock_read_text.assert_not_called()


@unittest.skipUnless(IS_WINDOWS, "drive-letter stripping is Windows-only behavior")
class TestWindowsDriveLetterStripping(unittest.TestCase):
    def test_strips_leading_slash(self):
        self.assertEqual(
            uri_to_path("file:///C:/Users/user/project/file.py"),
            Path("C:/Users/user/project/file.py").resolve(),
        )

    def test_encoded_spaces(self):
        self.assertEqual(
            uri_to_path("file:///C:/Users/My%20Documents/project/file.py"),
            Path("C:/Users/My Documents/project/file.py").resolve(),
        )

    def test_percent_encoded_drive(self):
        self.assertEqual(
            uri_to_path("file:///d%3A/a/repo/src/index.js"),
            Path("d:/a/repo/src/index.js").resolve(),
        )


@unittest.skipUnless(IS_WINDOWS, "case-canonicalization is Windows-only behavior")
class TestWindowsCaseCanonicalization(unittest.TestCase):
    def test_lowercase_uri_resolves_to_real_filesystem_case(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            real_dir = Path(tmpdir) / "SrcDir"
            real_dir.mkdir()
            real_file = real_dir / "Index.js"
            real_file.touch()

            uri = real_file.as_uri().lower()
            result = uri_to_path(uri)

            self.assertIsNotNone(result)
            self.assertEqual(str(result), str(real_file.resolve()))
