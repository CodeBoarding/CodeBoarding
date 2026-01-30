"""
Git diff analysis for incremental static analysis.

This module extends the existing git utilities to provide functionality
for identifying changed files since a specific commit, which is essential
for incremental analysis workflows.
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class GitDiffAnalyzer:
    """
    Analyzes git differences to identify changed files for incremental analysis.

    Extends existing git utilities with functionality needed for incremental
    static analysis workflows.
    """

    def __init__(self, repo_path: Path):
        """
        Initialize the git diff analyzer.

        Args:
            repo_path: Path to the git repository root

        Raises:
            ValueError: If repo_path is not a valid git repository
        """
        self.repo_path = repo_path

        if not self._is_git_repository():
            raise ValueError(f"Path {repo_path} is not a valid git repository")

    def get_changed_files(self, from_commit: str) -> set[Path]:
        """
        Get files that have changed since the specified commit.

        Includes both committed changes and uncommitted changes in the working directory.

        Args:
            from_commit: Git commit hash to compare against

        Returns:
            Set of Path objects representing changed files

        Raises:
            subprocess.CalledProcessError: If git command fails
        """
        changed_files = set()

        try:
            # Get committed changes since the specified commit
            result = subprocess.run(
                ["git", "diff", "--name-only", from_commit, "HEAD"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )

            for line in result.stdout.strip().split("\n"):
                if line:  # Skip empty lines
                    file_path = self.repo_path / line
                    # Include all changed files, even if they no longer exist (deleted files)
                    changed_files.add(file_path)

            # Get uncommitted changes (staged and unstaged)
            uncommitted_files = self._get_uncommitted_changes()
            changed_files.update(uncommitted_files)

            logger.info(f"Found {len(changed_files)} changed files since commit {from_commit}")
            return changed_files

        except subprocess.CalledProcessError as e:
            logger.error(f"Git diff command failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Error getting changed files: {e}")
            raise

    def get_current_commit(self) -> str:
        """
        Get the current commit hash.

        Returns:
            Current commit hash as a string

        Raises:
            subprocess.CalledProcessError: If git command fails
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.repo_path, capture_output=True, text=True, check=True
            )

            commit_hash = result.stdout.strip()
            logger.debug(f"Current commit hash: {commit_hash}")
            return commit_hash

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get current commit hash: {e}")
            raise

    def has_uncommitted_changes(self) -> bool:
        """
        Check if there are uncommitted changes in the working directory.

        Returns:
            True if there are uncommitted changes, False otherwise
        """
        try:
            # Check for staged changes
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )

            if result.stdout.strip():
                return True

            # Check for unstaged changes
            result = subprocess.run(
                ["git", "diff", "--name-only"], cwd=self.repo_path, capture_output=True, text=True, check=True
            )

            if result.stdout.strip():
                return True

            # Check for untracked files
            result = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )

            if result.stdout.strip():
                return True

            return False

        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to check for uncommitted changes: {e}")
            return False

    def _get_uncommitted_changes(self) -> set[Path]:
        """
        Get files with uncommitted changes (staged, unstaged, and untracked).

        Returns:
            Set of Path objects representing files with uncommitted changes
        """
        changed_files = set()

        try:
            # Get staged changes
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )

            for line in result.stdout.strip().split("\n"):
                if line:
                    file_path = self.repo_path / line
                    if file_path.exists():
                        changed_files.add(file_path)

            # Get unstaged changes
            result = subprocess.run(
                ["git", "diff", "--name-only"], cwd=self.repo_path, capture_output=True, text=True, check=True
            )

            for line in result.stdout.strip().split("\n"):
                if line:
                    file_path = self.repo_path / line
                    if file_path.exists():
                        changed_files.add(file_path)

            # Get untracked files (but exclude ignored files)
            result = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True,
            )

            for line in result.stdout.strip().split("\n"):
                if line:
                    file_path = self.repo_path / line
                    if file_path.exists():
                        changed_files.add(file_path)

        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to get uncommitted changes: {e}")

        return changed_files

    def _is_git_repository(self) -> bool:
        """
        Check if the given path is a valid git repository.

        Returns:
            True if it's a valid git repository, False otherwise
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"], cwd=self.repo_path, capture_output=True, text=True, check=True
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
