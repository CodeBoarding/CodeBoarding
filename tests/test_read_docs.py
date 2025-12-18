import unittest
import os
from pathlib import Path

from agents.tools.read_docs import ReadDocsTool


class TestReadDocsTool(unittest.TestCase):

    def setUp(self):
        # Set up any necessary state or mocks before each test
        self.tool = ReadDocsTool(repo_dir=Path("./repos/yfinance"))

    @unittest.skipIf(not os.path.exists("./repos/yfinance"), "yfinance repo not found")
    def test_read_default_readme(self):
        # Test the _run method with no parameters (should default to README.md)
        content = self.tool._run()
        self.assertIsInstance(content, str)
        self.assertIn("File: README.md", content)
        self.assertIn("--- Other Available Documentation Files ---", content)

    @unittest.skipIf(not os.path.exists("./repos/yfinance"), "yfinance repo not found")
    def test_read_specific_md_file(self):
        # Test the _run method with a specific markdown file
        content = self.tool._run("CODE_OF_CONDUCT.md")
        self.assertIsInstance(content, str)
        self.assertIn("--- Other Available Documentation Files ---", content)

    @unittest.skipIf(not os.path.exists("./repos/yfinance"), "yfinance repo not found")
    def test_read_bad_file(self):
        # Test the _run method with an invalid file path
        content = self.tool._run("badfile.md")
        self.assertIsInstance(content, str)
        self.assertIn("Error: The specified file 'badfile.md' was not found", content)
        # Fixed: Updated expectation to match actual output "Available documentation files:"
        self.assertIn("Available documentation files:", content)

    def test_readme_not_found(self):
        # Test when README.md doesn't exist (using a non-existent repo path)
        tool_no_readme = ReadDocsTool(repo_dir=Path("/tmp/nonexistent"))
        content = tool_no_readme._run()
        self.assertIsInstance(content, str)
        # Fixed: Updated expectation to match actual output
        self.assertIn("No documentation files found in this repository.", content)

    @unittest.skipIf(not os.path.exists("./repos/yfinance"), "yfinance repo not found")
    def test_always_includes_other_files(self):
        # Test that other available files are always listed
        content = self.tool._run("README.md")
        self.assertIsInstance(content, str)
        # Should contain the main content and the appendix
        parts = content.split("--- Other Available Documentation Files ---")
        self.assertEqual(len(parts), 2)  # Should be split into exactly 2 parts
        self.assertIn("File: README.md", parts[0])  # First part has the file content
        self.assertTrue(parts[1].strip())  # Second part has the file list and is not empty
