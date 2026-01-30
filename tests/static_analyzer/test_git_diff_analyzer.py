"""Tests for the GitDiffAnalyzer class."""

import tempfile
import shutil
import subprocess
import unittest
from pathlib import Path

from static_analyzer.git_diff_analyzer import GitDiffAnalyzer


class TestGitDiffAnalyzer(unittest.TestCase):
    """Tests for GitDiffAnalyzer functionality."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir)

        # Initialize a git repository
        subprocess.run(["git", "init"], cwd=self.repo_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_path, check=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init_with_valid_git_repo(self):
        """GitDiffAnalyzer should initialize successfully with a valid git repo."""
        analyzer = GitDiffAnalyzer(self.repo_path)
        self.assertEqual(analyzer.repo_path, self.repo_path)

    def test_init_with_invalid_path_raises_error(self):
        """GitDiffAnalyzer should raise ValueError for non-git directory."""
        # Create a completely separate temp directory outside of any git repo
        import tempfile

        separate_temp = tempfile.mkdtemp()
        try:
            non_git_path = Path(separate_temp) / "not_git"
            non_git_path.mkdir()

            with self.assertRaises(ValueError) as context:
                GitDiffAnalyzer(non_git_path)

            self.assertIn("not a valid git repository", str(context.exception))
        finally:
            shutil.rmtree(separate_temp, ignore_errors=True)

    def test_get_current_commit_with_commits(self):
        """get_current_commit should return current commit hash."""
        # Create and commit a file
        test_file = self.repo_path / "test.py"
        test_file.write_text("print('hello')")
        subprocess.run(["git", "add", "test.py"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.repo_path, check=True)

        analyzer = GitDiffAnalyzer(self.repo_path)
        commit_hash = analyzer.get_current_commit()

        self.assertIsInstance(commit_hash, str)
        self.assertEqual(len(commit_hash), 40)  # SHA-1 hash length

    def test_has_uncommitted_changes_with_no_changes(self):
        """has_uncommitted_changes should return False when no changes exist."""
        # Create and commit a file
        test_file = self.repo_path / "test.py"
        test_file.write_text("print('hello')")
        subprocess.run(["git", "add", "test.py"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.repo_path, check=True)

        analyzer = GitDiffAnalyzer(self.repo_path)
        self.assertFalse(analyzer.has_uncommitted_changes())

    def test_has_uncommitted_changes_with_unstaged_changes(self):
        """has_uncommitted_changes should return True for unstaged changes."""
        # Create and commit a file
        test_file = self.repo_path / "test.py"
        test_file.write_text("print('hello')")
        subprocess.run(["git", "add", "test.py"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.repo_path, check=True)

        # Modify the file
        test_file.write_text("print('hello world')")

        analyzer = GitDiffAnalyzer(self.repo_path)
        self.assertTrue(analyzer.has_uncommitted_changes())

    def test_has_uncommitted_changes_with_staged_changes(self):
        """has_uncommitted_changes should return True for staged changes."""
        # Create and commit a file
        test_file = self.repo_path / "test.py"
        test_file.write_text("print('hello')")
        subprocess.run(["git", "add", "test.py"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.repo_path, check=True)

        # Modify and stage the file
        test_file.write_text("print('hello world')")
        subprocess.run(["git", "add", "test.py"], cwd=self.repo_path, check=True)

        analyzer = GitDiffAnalyzer(self.repo_path)
        self.assertTrue(analyzer.has_uncommitted_changes())

    def test_has_uncommitted_changes_with_untracked_files(self):
        """has_uncommitted_changes should return True for untracked files."""
        # Create and commit a file
        test_file = self.repo_path / "test.py"
        test_file.write_text("print('hello')")
        subprocess.run(["git", "add", "test.py"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.repo_path, check=True)

        # Create an untracked file
        untracked_file = self.repo_path / "untracked.py"
        untracked_file.write_text("print('untracked')")

        analyzer = GitDiffAnalyzer(self.repo_path)
        self.assertTrue(analyzer.has_uncommitted_changes())

    def test_get_changed_files_between_commits(self):
        """get_changed_files should return files changed between commits."""
        # Create initial commit
        file1 = self.repo_path / "file1.py"
        file1.write_text("print('file1')")
        subprocess.run(["git", "add", "file1.py"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.repo_path, check=True)

        # Get the initial commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_path, capture_output=True, text=True, check=True
        )
        initial_commit = result.stdout.strip()

        # Create second commit with changes
        file2 = self.repo_path / "file2.py"
        file2.write_text("print('file2')")
        file1.write_text("print('file1 modified')")  # Modify existing file
        subprocess.run(["git", "add", "."], cwd=self.repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "Second commit"], cwd=self.repo_path, check=True)

        analyzer = GitDiffAnalyzer(self.repo_path)
        changed_files = analyzer.get_changed_files(initial_commit)

        # Should include both the modified file and the new file
        changed_file_names = {f.name for f in changed_files}
        self.assertIn("file1.py", changed_file_names)
        self.assertIn("file2.py", changed_file_names)

    def test_get_changed_files_includes_uncommitted_changes(self):
        """get_changed_files should include uncommitted changes."""
        # Create initial commit
        file1 = self.repo_path / "file1.py"
        file1.write_text("print('file1')")
        subprocess.run(["git", "add", "file1.py"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.repo_path, check=True)

        # Get the commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_path, capture_output=True, text=True, check=True
        )
        commit_hash = result.stdout.strip()

        # Make uncommitted changes
        file2 = self.repo_path / "file2.py"
        file2.write_text("print('file2')")  # Untracked file
        file1.write_text("print('file1 modified')")  # Modified file

        analyzer = GitDiffAnalyzer(self.repo_path)
        changed_files = analyzer.get_changed_files(commit_hash)

        # Should include both uncommitted files
        changed_file_names = {f.name for f in changed_files}
        self.assertIn("file1.py", changed_file_names)
        self.assertIn("file2.py", changed_file_names)

    def test_get_changed_files_empty_when_no_changes(self):
        """get_changed_files should return empty set when no changes exist."""
        # Create initial commit
        file1 = self.repo_path / "file1.py"
        file1.write_text("print('file1')")
        subprocess.run(["git", "add", "file1.py"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.repo_path, check=True)

        # Get the commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_path, capture_output=True, text=True, check=True
        )
        commit_hash = result.stdout.strip()

        analyzer = GitDiffAnalyzer(self.repo_path)
        changed_files = analyzer.get_changed_files(commit_hash)

        self.assertEqual(len(changed_files), 0)


if __name__ == "__main__":
    unittest.main()
