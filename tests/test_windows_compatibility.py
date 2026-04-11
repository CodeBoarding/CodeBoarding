"""Tests for Windows-sensitive path handling."""

import platform
import tempfile
import unittest
from pathlib import Path

from static_analyzer.engine.utils import uri_to_path

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
