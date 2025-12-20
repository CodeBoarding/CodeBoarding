import unittest
from pathlib import Path

from agents.tools.read_git_diff import ReadDiffTool
from agents.tools.base import RepoContext
from repo_utils.ignore import RepoIgnoreManager
from repo_utils.git_diff import FileChange


class TestReadDiffTool(unittest.TestCase):

    def setUp(self):
        # Create sample file changes
        self.file_changes = [
            FileChange(
                filename="example.py",
                additions=2,
                deletions=1,
                added_lines=["def new_function():", "    return 42"],
                removed_lines=["old_code = True"],
            ),
            FileChange(
                filename="src/module.py",
                additions=1,
                deletions=1,
                added_lines=["import new_module"],
                removed_lines=["import old_module"],
            ),
        ]
        repo_dir = Path(".")
        ignore_manager = RepoIgnoreManager(repo_dir)
        self.context = RepoContext(repo_dir=repo_dir, ignore_manager=ignore_manager)
        self.tool = ReadDiffTool(context=self.context, diffs=self.file_changes)

    def test_read_diff_basic(self):
        # Test basic diff reading
        content = self.tool._run("example.py", 1)
        self.assertIn("example.py", content)
        self.assertIn("Total additions: 2", content)
        self.assertIn("Total deletions: 1", content)
        self.assertIn("+ def new_function():", content)
        self.assertIn("- old_code = True", content)

    def test_read_diff_partial_match(self):
        # Test partial path matching
        content = self.tool._run("module.py", 1)
        self.assertIn("src/module.py", content)
        self.assertIn("+ import new_module", content)
        self.assertIn("- import old_module", content)

    def test_read_diff_file_not_found(self):
        # Test error handling for non-existent file
        content = self.tool._run("nonexistent.py", 1)
        self.assertIn("Error: No diff found", content)
        self.assertIn("Available files with changes:", content)
        self.assertIn("example.py", content)

    def test_read_diff_no_diffs(self):
        # Test with no diffs available
        empty_tool = ReadDiffTool(context=self.context, diffs=[])
        content = empty_tool._run("example.py", 1)
        self.assertIn("Error: No diff information available", content)

    def test_read_diff_empty_changes(self):
        # Test file with no actual line changes
        empty_change = FileChange(filename="binary.bin", additions=0, deletions=0, added_lines=[], removed_lines=[])
        tool = ReadDiffTool(context=self.context, diffs=[empty_change])
        content = tool._run("binary.bin", 1)
        self.assertIn("No detailed line changes available", content)

    def test_read_diff_pagination(self):
        # Test pagination with large diff
        large_changes = FileChange(
            filename="large.py",
            additions=150,
            deletions=0,
            added_lines=[f"line_{i}" for i in range(150)],
            removed_lines=[],
        )
        tool = ReadDiffTool(context=self.context, diffs=[large_changes])
        content = tool._run("large.py", 1)
        self.assertIn("DIFF TRUNCATED", content)
        self.assertIn("Showing lines", content)

    def test_read_diff_line_out_of_range(self):
        # Test line number validation
        content = self.tool._run("example.py", 999)
        self.assertIn("Error: Line number 999 is out of range", content)
