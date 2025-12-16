import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_utils.git_diff import FileChange, get_git_diff


class TestFileChange(unittest.TestCase):
    def test_file_change_creation(self):
        # Test creating a FileChange object
        change = FileChange(
            filename="test.py",
            additions=10,
            deletions=5,
            added_lines=["line1", "line2"],
            removed_lines=["old_line"],
        )

        self.assertEqual(change.filename, "test.py")
        self.assertEqual(change.additions, 10)
        self.assertEqual(change.deletions, 5)
        self.assertEqual(len(change.added_lines), 2)
        self.assertEqual(len(change.removed_lines), 1)

    def test_file_change_default_lines(self):
        # Test FileChange with default empty lists
        change = FileChange(
            filename="test.py",
            additions=5,
            deletions=3,
        )

        self.assertEqual(len(change.added_lines), 0)
        self.assertEqual(len(change.removed_lines), 0)

    def test_file_change_llm_str(self):
        # Test LLM string representation
        change = FileChange(
            filename="src/module.py",
            additions=15,
            deletions=8,
        )

        llm_str = change.llm_str()

        self.assertIn("src/module.py", llm_str)
        self.assertIn("+15", llm_str)
        self.assertIn("-8", llm_str)
        self.assertIn("File:", llm_str)

    def test_file_change_llm_str_zero_changes(self):
        # Test LLM string with zero changes
        change = FileChange(
            filename="unchanged.py",
            additions=0,
            deletions=0,
        )

        llm_str = change.llm_str()

        self.assertIn("unchanged.py", llm_str)
        self.assertIn("+0", llm_str)
        self.assertIn("-0", llm_str)


class TestGetGitDiff(unittest.TestCase):
    @patch("git.Repo")
    def test_get_git_diff_basic(self, mock_repo_class):
        # Test basic git diff functionality
        mock_repo = MagicMock()
        mock_repo_class.return_value = mock_repo

        # Mock git diff output
        diff_output = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,3 +1,4 @@
 def test():
+    print("new line")
     pass
-    old_line()
"""
        mock_repo.git.diff.return_value = diff_output

        repo_dir = Path("/tmp/test_repo")
        version = "HEAD~1"

        changes = get_git_diff(repo_dir, version)

        self.assertGreater(len(changes), 0)
        mock_repo.git.diff.assert_called_once_with(version, "--patch")

    @patch("git.Repo")
    def test_get_git_diff_multiple_files(self, mock_repo_class):
        # Test diff with multiple files
        mock_repo = MagicMock()
        mock_repo_class.return_value = mock_repo

        diff_output = """diff --git a/file1.py b/file1.py
index 1111111..2222222 100644
--- a/file1.py
+++ b/file1.py
@@ -1,2 +1,3 @@
 line1
+added_line
 line2
diff --git a/file2.py b/file2.py
index 3333333..4444444 100644
--- a/file2.py
+++ b/file2.py
@@ -1,3 +1,2 @@
 line1
-removed_line
 line2
"""
        mock_repo.git.diff.return_value = diff_output

        repo_dir = Path("/tmp/test_repo")
        changes = get_git_diff(repo_dir, "HEAD~1")

        # Should detect multiple files
        self.assertGreater(len(changes), 0)

    @patch("git.Repo")
    def test_get_git_diff_no_changes(self, mock_repo_class):
        # Test with no changes
        mock_repo = MagicMock()
        mock_repo_class.return_value = mock_repo

        mock_repo.git.diff.return_value = ""

        repo_dir = Path("/tmp/test_repo")
        changes = get_git_diff(repo_dir, "HEAD")

        self.assertEqual(len(changes), 0)

    @patch("git.Repo")
    def test_get_git_diff_exception_handling(self, mock_repo_class):
        # Test exception handling
        mock_repo_class.side_effect = Exception("Git error")

        repo_dir = Path("/tmp/test_repo")

        # Should handle the exception gracefully and return empty list
        changes = get_git_diff(repo_dir, "HEAD~1")
        self.assertEqual(len(changes), 0)

    @patch("git.Repo")
    def test_get_git_diff_with_commit_hash(self, mock_repo_class):
        # Test with specific commit hash
        mock_repo = MagicMock()
        mock_repo_class.return_value = mock_repo

        diff_output = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,1 +1,2 @@
 original
+new line
"""
        mock_repo.git.diff.return_value = diff_output

        repo_dir = Path("/tmp/test_repo")
        commit_hash = "abc123def456"

        changes = get_git_diff(repo_dir, commit_hash)

        self.assertGreater(len(changes), 0)
        mock_repo.git.diff.assert_called_once_with(commit_hash, "--patch")

    @patch("git.Repo")
    def test_get_git_diff_with_tag(self, mock_repo_class):
        # Test with git tag
        mock_repo = MagicMock()
        mock_repo_class.return_value = mock_repo

        diff_output = """diff --git a/version.py b/version.py
index 1111111..2222222 100644
--- a/version.py
+++ b/version.py
@@ -1,1 +1,1 @@
-VERSION = "1.0.0"
+VERSION = "1.1.0"
"""
        mock_repo.git.diff.return_value = diff_output

        repo_dir = Path("/tmp/test_repo")
        tag = "v1.0.0"

        changes = get_git_diff(repo_dir, tag)

        self.assertGreater(len(changes), 0)
        mock_repo.git.diff.assert_called_once_with(tag, "--patch")


if __name__ == "__main__":
    unittest.main()
