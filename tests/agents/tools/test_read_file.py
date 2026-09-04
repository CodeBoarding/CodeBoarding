import unittest
import tempfile
from pathlib import Path

from agents.tools.read_file import ReadFileTool
from agents.tools.base import RepoContext


class TestReadFileTool(unittest.TestCase):

    def setUp(self):
        # Set up any necessary state or mocks before each test
        test_repo = Path("./test-vscode-repo")
        if not test_repo.exists():
            self.skipTest("Test repository not available")
        context = RepoContext(repo_dir=test_repo)
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


class TestScopedReadFileTool(unittest.TestCase):
    def test_reads_only_exact_files_in_the_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            repo_dir = Path(directory)
            (repo_dir / "allowed.py").write_text("first\nsecond\n", encoding="utf-8")
            (repo_dir / "secret.py").write_text("secret\n", encoding="utf-8")
            context = RepoContext(
                repo_dir=repo_dir,
                scope_restricted=True,
                scope_files=frozenset({"allowed.py"}),
            )
            tool = ReadFileTool(context=context)

            self.assertIn("first", tool._run("allowed.py", 1))
            self.assertIn("outside the current analysis scope", tool._run("secret.py", 1))
            self.assertIn("outside the current analysis scope", tool._run("../secret.py", 1))

    def test_rejects_repository_traversal_without_a_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            repo_dir = Path(directory) / "repo"
            repo_dir.mkdir()
            context = RepoContext(repo_dir=repo_dir)

            self.assertIn("outside the repository", ReadFileTool(context=context)._run("../secret.py", 1))
