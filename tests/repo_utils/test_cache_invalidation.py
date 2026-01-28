import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from repo_utils import get_repo_state_hash
from repo_utils.ignore import RepoIgnoreManager


class TestCacheInvalidationWithIgnorePatterns(unittest.TestCase):

    @patch("repo_utils.Repo")
    def test_state_hash_ignores_test_directory_untracked(self, mock_repo_class):
        """State hash should not change when untracked files are only in test directories."""
        mock_repo = MagicMock()
        mock_repo.head.commit.hexsha = "abc123def456789"
        mock_repo.git.diff.return_value = ""
        mock_repo.untracked_files = [
            "tests/new_test.py",
            "test/another_test.py",
        ]
        mock_repo_class.return_value = mock_repo

        # Since all untracked files are in ignored dirs, the hash should be consistent
        with patch("repo_utils.GIT_AVAILABLE", True):
            hash1 = get_repo_state_hash("/fake/repo")

        # Now add more untracked files in ignored dirs
        mock_repo.untracked_files = [
            "tests/new_test.py",
            "tests/more_tests.py",
            "node_modules/package.json",
        ]

        with patch("repo_utils.GIT_AVAILABLE", True):
            hash2 = get_repo_state_hash("/fake/repo")

        # Both should have the same hash since all untracked are in ignored dirs
        self.assertEqual(hash1, hash2)

    @patch("repo_utils.Repo")
    def test_state_hash_changes_with_source_untracked(self, mock_repo_class):
        """State hash should change when untracked files are in source directories."""
        mock_repo = MagicMock()
        mock_repo.head.commit.hexsha = "abc123def456789"
        mock_repo.git.diff.return_value = ""
        mock_repo.untracked_files = []
        mock_repo_class.return_value = mock_repo

        with patch("repo_utils.GIT_AVAILABLE", True):
            hash_no_untracked = get_repo_state_hash("/fake/repo")

        # Add untracked file in source directory
        mock_repo.untracked_files = ["src/new_feature.py"]

        with patch("repo_utils.GIT_AVAILABLE", True):
            hash_with_untracked = get_repo_state_hash("/fake/repo")

        # Hash should be different
        self.assertNotEqual(hash_no_untracked, hash_with_untracked)


class TestIgnorePatternEdgeCases(unittest.TestCase):
    """Edge cases for ignore pattern matching."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir)
        (self.repo_path / ".codeboarding").mkdir()
        self.ignore_manager = RepoIgnoreManager(self.repo_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_hidden_files_are_ignored(self):
        """Hidden files and directories should be ignored."""
        hidden_paths = [
            Path(".env"),
            Path(".vscode/settings.json"),
            Path(".idea/workspace.xml"),
            Path(".cache/data"),
        ]

        for path in hidden_paths:
            self.assertTrue(
                self.ignore_manager.should_ignore(path),
                f"Hidden path {path} should be ignored",
            )

    def test_minified_files_are_ignored(self):
        """Minified and bundled files should be ignored."""
        minified_paths = [
            Path("dist/app.min.js"),
            Path("build/bundle.js"),
            Path("out/app.bundle.js"),
            Path("static/styles.min.css"),
        ]

        for path in minified_paths:
            self.assertTrue(
                self.ignore_manager.should_ignore(path),
                f"Minified/bundled file {path} should be ignored",
            )

    def test_source_map_files_are_ignored(self):
        """Source map files should be ignored."""
        source_map_paths = [
            Path("dist/app.bundle.js.map"),
            Path("build/main.chunk.js.map"),
        ]

        for path in source_map_paths:
            self.assertTrue(
                self.ignore_manager.should_ignore(path),
                f"Source map {path} should be ignored",
            )

    def test_pycache_anywhere_is_ignored(self):
        """__pycache__ directories at any level should be ignored."""
        pycache_paths = [
            Path("__pycache__/module.cpython-312.pyc"),
            Path("src/__pycache__/utils.cpython-312.pyc"),
            Path("lib/package/__pycache__/helper.cpython-312.pyc"),
        ]

        for path in pycache_paths:
            self.assertTrue(
                self.ignore_manager.should_ignore(path),
                f"__pycache__ path {path} should be ignored",
            )


if __name__ == "__main__":
    unittest.main()
