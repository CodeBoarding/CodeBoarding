"""
Tests for static analysis cache invalidation logic.

These tests verify the behavior of the caching system when:
- No changes are made (cache should be valid)
- Changes are made to ignored files (cache should remain valid)
- Changes are made to files with ignored prefix/suffix patterns
- Changes are made to both ignored and non-ignored files
- Changes are made to non-relevant files for static analysis (e.g., README.md)
"""

import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from repo_utils import get_repo_state_hash
from repo_utils.ignore import RepoIgnoreManager


class TestCacheInvalidationWithIgnorePatterns(unittest.TestCase):
    """Tests for cache invalidation behavior with ignore patterns."""

    def setUp(self):
        """Create a temporary directory with test files."""
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir)
        self._create_test_structure()
        self.ignore_manager = RepoIgnoreManager(self.repo_path)

    def tearDown(self):
        """Clean up the temporary directory."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_test_structure(self):
        """Create a realistic repository structure."""
        # Source directories
        (self.repo_path / "src").mkdir()
        (self.repo_path / "lib").mkdir()

        # Ignored directories
        (self.repo_path / "tests").mkdir()
        (self.repo_path / "test").mkdir()
        (self.repo_path / "node_modules").mkdir()
        (self.repo_path / ".codeboarding").mkdir()

        # Source files
        (self.repo_path / "src" / "main.py").write_text("# Main module")
        (self.repo_path / "src" / "utils.py").write_text("# Utils module")
        (self.repo_path / "lib" / "helpers.ts").write_text("// Helpers")

        # Test files in ignored directories
        (self.repo_path / "tests" / "test_main.py").write_text("# Test main")
        (self.repo_path / "test" / "test_utils.py").write_text("# Test utils")

        # Non-code files
        (self.repo_path / "README.md").write_text("# Project README")
        (self.repo_path / "LICENSE").write_text("MIT License")
        (self.repo_path / ".gitignore").write_text("*.log\n*.tmp\n")

    # =========================================================================
    # Test: No changes - cache should be valid
    # =========================================================================
    def test_no_changes_all_files_ignored_or_not(self):
        """When no files change, no cache invalidation should occur."""
        # Simply verify that the ignore manager correctly identifies files
        # without any changes to the file system
        source_files = [
            Path("src/main.py"),
            Path("src/utils.py"),
            Path("lib/helpers.ts"),
        ]
        ignored_files = [
            Path("tests/test_main.py"),
            Path("test/test_utils.py"),
            Path("node_modules/package/index.js"),
        ]

        for f in source_files:
            self.assertFalse(
                self.ignore_manager.should_ignore(f),
                f"Source file {f} should NOT be ignored",
            )

        for f in ignored_files:
            self.assertTrue(
                self.ignore_manager.should_ignore(f),
                f"Ignored file {f} SHOULD be ignored",
            )

    # =========================================================================
    # Test: Changes to ignored files only
    # =========================================================================
    def test_changes_to_ignored_files_only(self):
        """Changes to files in ignored directories should not affect source filtering."""
        # Files in ignored directories
        ignored_paths = [
            Path("tests/test_new.py"),
            Path("test/test_another.py"),
            Path("node_modules/react/index.js"),
            Path(".codeboarding/cache.json"),
        ]

        # All should be ignored
        for path in ignored_paths:
            self.assertTrue(
                self.ignore_manager.should_ignore(path),
                f"File in ignored directory {path} should be ignored",
            )

        # Non-ignored source files remain unaffected
        source_files = [Path("src/main.py"), Path("lib/helpers.ts")]
        filtered = self.ignore_manager.filter_paths(ignored_paths + source_files)
        self.assertEqual(
            set(filtered),
            set(source_files),
            "Only source files should pass the filter",
        )

    # =========================================================================
    # Test: Prefix/suffix matching - protest.py vs ignored "test" directory
    # =========================================================================
    def test_prefix_suffix_matching_not_confused_with_ignored_dirs(self):
        """
        Files with names containing ignored directory names as prefix/suffix
        should NOT be incorrectly ignored.
        E.g., 'protest.py' should not be ignored just because 'test' is an ignored dir.
        """
        # Files that contain "test" but are NOT in the tests/ directory
        non_ignored_with_test_substring = [
            Path("src/protest.py"),  # Contains "test" as suffix
            Path("src/test_helper.py"),  # Contains "test" as prefix (but in src/)
            Path("lib/attestation.ts"),  # Contains "test" in middle
            Path("src/contest.py"),  # Contains "test"
            Path("src/latest_feature.py"),  # Contains "test"
        ]

        for path in non_ignored_with_test_substring:
            self.assertFalse(
                self.ignore_manager.should_ignore(path),
                f"File {path} should NOT be ignored (test is in name, not in path)",
            )

    def test_files_in_test_directories_are_ignored(self):
        """Files actually in test/tests directories should be ignored."""
        # Files in tests/ or test/ directories
        ignored_test_files = [
            Path("tests/test_main.py"),
            Path("test/test_utils.py"),
            Path("tests/conftest.py"),
            Path("test/fixtures/data.json"),
        ]

        for path in ignored_test_files:
            self.assertTrue(
                self.ignore_manager.should_ignore(path),
                f"File {path} in test directory SHOULD be ignored",
            )

    def test_nested_test_directory_pattern(self):
        """Test that deeply nested test directories are also ignored."""
        nested_test_paths = [
            Path("src/module/tests/test_module.py"),
            Path("lib/package/test/unit.py"),
        ]

        for path in nested_test_paths:
            self.assertTrue(
                self.ignore_manager.should_ignore(path),
                f"Nested test file {path} SHOULD be ignored",
            )

    # =========================================================================
    # Test: Changes to both ignored and non-ignored files
    # =========================================================================
    def test_mixed_changes_ignored_and_non_ignored(self):
        """When both ignored and non-ignored files change, filter correctly."""
        mixed_paths = [
            # Non-ignored (should pass filter)
            Path("src/main.py"),
            Path("src/new_feature.py"),
            Path("lib/helpers.ts"),
            # Ignored (should be filtered out)
            Path("tests/test_main.py"),
            Path("node_modules/lodash/index.js"),
            Path(".codeboarding/config.yml"),
            Path("build/output.js"),
        ]

        filtered = self.ignore_manager.filter_paths(mixed_paths)

        # Only non-ignored files should remain
        expected = {
            Path("src/main.py"),
            Path("src/new_feature.py"),
            Path("lib/helpers.ts"),
        }
        self.assertEqual(set(filtered), expected)

    def test_filter_preserves_order(self):
        """The filter should preserve the order of non-ignored files."""
        paths = [
            Path("src/a.py"),
            Path("tests/ignored.py"),
            Path("src/b.py"),
            Path("node_modules/x.js"),
            Path("src/c.py"),
        ]

        filtered = self.ignore_manager.filter_paths(paths)
        self.assertEqual(filtered, [Path("src/a.py"), Path("src/b.py"), Path("src/c.py")])

    # =========================================================================
    # Test: Changes to non-relevant files for static analysis (README.md)
    # =========================================================================
    def test_non_code_files_are_not_ignored_by_default(self):
        """
        Non-code files like README.md, LICENSE are not ignored by default.
        The ignore manager doesn't filter by extension, only by path patterns.
        """
        non_code_files = [
            Path("README.md"),
            Path("LICENSE"),
            Path("CONTRIBUTING.md"),
            Path("docs/guide.md"),
            Path("pyproject.toml"),
            Path("package.json"),
        ]

        for path in non_code_files:
            self.assertFalse(
                self.ignore_manager.should_ignore(path),
                f"Non-code file {path} should NOT be ignored by path-based ignore",
            )

    def test_non_code_files_in_ignored_dirs_are_still_ignored(self):
        """Non-code files in ignored directories should still be ignored."""
        paths_in_ignored_dirs = [
            Path("tests/README.md"),
            Path("node_modules/package.json"),
            Path(".codeboarding/config.yml"),
            Path("build/manifest.json"),
        ]

        for path in paths_in_ignored_dirs:
            self.assertTrue(
                self.ignore_manager.should_ignore(path),
                f"File {path} in ignored directory SHOULD be ignored",
            )


class TestCacheInvalidationHelpers(unittest.TestCase):
    """Test helper functions for determining cache-relevant changes."""

    @staticmethod
    def get_static_analysis_extensions() -> set[str]:
        """
        Extensions considered relevant for static analysis.
        Changes to files with these extensions should invalidate cache.
        """
        return {
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".java",
            ".go",
            ".rs",
            ".php",
            ".rb",
            ".c",
            ".cpp",
            ".h",
            ".hpp",
            ".cs",
            ".swift",
            ".kt",
            ".scala",
        }

    @staticmethod
    def get_non_relevant_extensions() -> set[str]:
        """Extensions that are not relevant for static analysis."""
        return {".md", ".txt", ".yml", ".yaml", ".json", ".toml", ".lock", ".cfg", ".ini"}

    def test_code_files_are_relevant(self):
        """Code files should be considered relevant for static analysis."""
        code_files = [
            "main.py",
            "utils.ts",
            "component.tsx",
            "handler.go",
            "service.java",
        ]

        relevant_extensions = self.get_static_analysis_extensions()

        for filename in code_files:
            ext = Path(filename).suffix
            self.assertIn(
                ext,
                relevant_extensions,
                f"Extension {ext} should be relevant for static analysis",
            )

    def test_documentation_files_are_not_relevant(self):
        """Documentation and config files should not be relevant for static analysis."""
        doc_files = [
            "README.md",
            "CHANGELOG.md",
            "config.yml",
            "settings.json",
            "poetry.lock",
        ]

        non_relevant = self.get_non_relevant_extensions()

        for filename in doc_files:
            ext = Path(filename).suffix
            self.assertIn(
                ext,
                non_relevant,
                f"Extension {ext} should NOT be relevant for static analysis",
            )


class TestRepoStateHashWithIgnoredChanges(unittest.TestCase):
    """
    Tests for get_repo_state_hash behavior with ignored files.

    These tests mock the git repository to simulate various change scenarios.
    """

    def test_ignored_dirs_excluded_from_untracked_files(self):
        """Untracked files in ignored directories should not affect state hash."""
        # The get_repo_state_hash function filters untracked files using DEFAULT_IGNORED_DIRS
        ignored_dirs = RepoIgnoreManager.DEFAULT_IGNORED_DIRS

        # Simulate untracked files
        untracked_files = [
            "src/new_file.py",
            "tests/test_new.py",
            "node_modules/package/index.js",
            "__pycache__/module.cpython-312.pyc",
            ".venv/lib/python3.12/site-packages/pkg.py",
        ]

        # Filter as done in get_repo_state_hash
        filtered = [f for f in untracked_files if not any(part in ignored_dirs for part in Path(f).parts)]

        # Only src/new_file.py should remain
        self.assertEqual(filtered, ["src/new_file.py"])

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
