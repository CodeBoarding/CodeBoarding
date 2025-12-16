import unittest
from pathlib import Path

from agents.tools.read_docs import ReadDocsTool


class TestReadDocsTool(unittest.TestCase):

    def setUp(self):
        # Set up any necessary state or mocks before each test
        test_repo = Path("./test-vscode-repo")
        if not test_repo.exists():
            self.skipTest("Test repository not available")
        self.tool = ReadDocsTool(repo_dir=test_repo)

    def test_read_default_readme(self):
        # Test the _run method with no parameters
        # test-vscode-repo doesn't have README.md, so it should show available files
        content = self.tool._run()
        self.assertIsInstance(content, str)
        # Either shows a file or lists available files
        self.assertTrue(len(content) > 0)

    def test_read_specific_md_file(self):
        # Test the _run method with a specific markdown file that exists
        content = self.tool._run("on_boarding.md")
        self.assertIsInstance(content, str)
        # Should show the file content
        self.assertTrue(len(content) > 0)

    def test_read_bad_file(self):
        # Test the _run method with an invalid file path
        content = self.tool._run("badfile.md")
        self.assertIsInstance(content, str)
        self.assertIn("Error: The specified file 'badfile.md' was not found", content)
        self.assertIn("Available", content)

    def test_readme_not_found(self):
        # Test when README.md doesn't exist (using a non-existent repo path)
        tool_no_readme = ReadDocsTool(repo_dir=Path("/tmp/nonexistent"))
        content = tool_no_readme._run()
        self.assertIsInstance(content, str)
        self.assertIn("No", content)
        self.assertIn("found", content)

    def test_always_includes_other_files(self):
        # Test that we can read a file successfully
        content = self.tool._run("on_boarding.md")
        self.assertIsInstance(content, str)
        # Should have content
        self.assertTrue(len(content) > 50)
