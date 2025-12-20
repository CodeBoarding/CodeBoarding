import unittest
from pathlib import Path

from agents.tools.read_file import ReadFileTool
from agents.tools.base import RepoContext
from repo_utils.ignore import RepoIgnoreManager


class TestReadFileTool(unittest.TestCase):

    def setUp(self):
        # Set up any necessary state or mocks before each test
        test_repo = Path("./test-vscode-repo")
        if not test_repo.exists():
            self.skipTest("Test repository not available")
        ignore_manager = RepoIgnoreManager(test_repo)
        context = RepoContext(repo_dir=test_repo, ignore_manager=ignore_manager)
        self.tool = ReadFileTool(context=context)

    def test_read_file(self):
        # Test the _run method with a valid file path - use an existing file
        content = self.tool._run("on_boarding.md", 1)
        self.assertIsInstance(content, str)
        # Should have some content with line numbers
        self.assertTrue(len(content) > 0)
        # Should have line number format
        self.assertIn(":", content)

    def test_read_bad_file(self):
        # Test the _run method with an invalid file path
        content = self.tool._run("badfile", 100)
        self.assertIsInstance(content, str)
        self.assertIn("Error: The specified file 'badfile' was not found in the indexed source files", content)
