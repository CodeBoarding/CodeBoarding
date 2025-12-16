"""
Tests to verify Windows compatibility fixes for path handling.
"""

import platform
import unittest
from pathlib import Path
import os

from static_analyzer.lsp_client.client import uri_to_path


class TestFileURIParsing(unittest.TestCase):
    """Test that file:// URI parsing works correctly on all platforms."""

    def test_unix_file_uri(self):
        """Test parsing Unix-style file URIs."""
        unix_uri = "file:///home/user/project/file.py"
        result = uri_to_path(unix_uri)

        expected = Path("/home/user/project/file.py")
        self.assertEqual(result, expected)

    def test_windows_file_uri(self):
        """Test parsing Windows-style file URIs."""
        windows_uri = "file:///C:/Users/user/project/file.py"
        result = uri_to_path(windows_uri)

        # Expected result depends on the platform:
        # On Windows: C:\Users\user\project\file.py (backslashes, but Path handles comparison)
        # On Unix: /C:/Users/user/project/file.py (url2pathname adds leading slash)
        if platform.system() == "Windows":
            expected = Path("C:/Users/user/project/file.py")
        else:
            # On Unix systems, url2pathname will convert file:///C:/... to /C:/...
            # This is expected behavior - the URI format isn't valid for Unix systems
            expected = Path("/C:/Users/user/project/file.py")

        self.assertEqual(result, expected)

    def test_windows_file_uri_with_encoded_spaces(self):
        """Test parsing Windows file URIs with URL-encoded spaces."""
        windows_uri = "file:///C:/Users/My%20Documents/project/file.py"
        result = uri_to_path(windows_uri)

        # Expected result depends on the platform
        if platform.system() == "Windows":
            expected = Path("C:/Users/My Documents/project/file.py")
        else:
            # On Unix systems, url2pathname will convert file:///C:/... to /C:/...
            expected = Path("/C:/Users/My Documents/project/file.py")

        self.assertEqual(result, expected)
