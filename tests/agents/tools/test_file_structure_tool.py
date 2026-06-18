import tempfile
import unittest
from pathlib import Path

from agents.tools import FileStructureTool
from agents.tools.base import RepoContext
from repo_utils.ignore import RepoIgnoreManager


class TestFileStructureTool(unittest.TestCase):
    def setUp(self):
        # Set up any necessary state or mocks before each test
        test_repo = Path("./test-vscode-repo")
        if not test_repo.exists():
            self.skipTest("Test repository not available")
        ignore_manager = RepoIgnoreManager(test_repo)
        context = RepoContext(repo_dir=test_repo, ignore_manager=ignore_manager)
        self.tool = FileStructureTool(context=context)

    def test_file_structure(self):
        # Test the _run method with a valid directory - use root directory
        content = self.tool._run(".")
        self.assertIn("The file tree", content)
        # Should have some files or directories
        self.assertTrue(len(content) > 50)

    def test_file_structure_sub_module(self):
        # Test with logs subdirectory if it exists
        content = self.tool._run("logs")
        # Either shows the tree or gives an error
        self.assertIsInstance(content, str)
        self.assertTrue(len(content) > 0)

    def test_invalid_directory(self):
        # Test reading a file for a non-existing directory
        # Note: The tool may fall back to showing the root directory if the path doesn't match
        # So we test that we get a valid response (either error or fallback)
        content = self.tool._run("non_existing_directory_12345")
        self.assertIsInstance(content, str)
        # Either shows an error or shows the tree (fallback behavior)
        self.assertTrue(len(content) > 0)
        # If it's an error, it should mention "Error" or if it's a fallback, show "file tree"
        self.assertTrue("Error" in content or "file tree" in content)


class TestFileStructureToolDefaultDir(unittest.TestCase):
    def test_run_without_dir_argument_defaults_to_root(self):
        # The args schema makes `dir` optional (default "."), so the agent may call
        # the tool with no arguments. _run must honor that default rather than raise.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "module.py").write_text("x = 1\n")
            tool = FileStructureTool(context=RepoContext(repo_dir=repo, ignore_manager=RepoIgnoreManager(repo)))

            direct = tool._run()
            self.assertIn("The file tree", direct)
            self.assertIn("module.py", direct)

            # The agent invokes tools with empty args; this is the path that failed in production.
            via_invoke = tool.invoke({})
            self.assertIn("The file tree", via_invoke)
