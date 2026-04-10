"""
Tests to verify Windows compatibility fixes for path handling.
"""

import unittest
from pathlib import Path

from static_analyzer.engine.utils import uri_to_path


class TestFileURIParsing(unittest.TestCase):
    """Test that file:// URI parsing works correctly on all platforms.

    uri_to_path is platform-independent — the Windows drive-letter strip
    runs unconditionally so a Linux dev machine parsing a Windows LSP
    fixture gets the same Path object a Windows machine would get.
    """

    def test_unix_file_uri(self):
        unix_uri = "file:///home/user/project/file.py"
        self.assertEqual(uri_to_path(unix_uri), Path("/home/user/project/file.py"))

    def test_windows_file_uri(self):
        """Windows URIs must lose the leading slash before the drive letter.

        Without the strip, ``Path("/C:/foo")`` on Windows is a drive-less
        absolute path that fails ``relative_to`` against any real project
        root, silently dropping every LSP reference.
        """
        self.assertEqual(
            uri_to_path("file:///C:/Users/user/project/file.py"),
            Path("C:/Users/user/project/file.py"),
        )

    def test_windows_file_uri_lowercase_drive(self):
        """Drive letter case is preserved (tsserver returns lowercase)."""
        self.assertEqual(
            uri_to_path("file:///d:/a/repo/src/index.js"),
            Path("d:/a/repo/src/index.js"),
        )

    def test_windows_file_uri_with_encoded_spaces(self):
        self.assertEqual(
            uri_to_path("file:///C:/Users/My%20Documents/project/file.py"),
            Path("C:/Users/My Documents/project/file.py"),
        )

    def test_windows_file_uri_with_percent_encoded_drive(self):
        """Some LSP servers emit the colon as %3A."""
        self.assertEqual(
            uri_to_path("file:///d%3A/a/repo/src/index.js"),
            Path("d:/a/repo/src/index.js"),
        )

    def test_empty_uri(self):
        self.assertIsNone(uri_to_path(""))

    def test_non_file_scheme(self):
        self.assertIsNone(uri_to_path("http://example.com/foo"))
