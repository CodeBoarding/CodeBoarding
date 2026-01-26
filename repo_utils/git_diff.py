import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class FileStatus(Enum):
    """Status of a file in a git diff."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


@dataclass
class FileChange:
    """
    Container for the changes made to a single file.
    """

    filename: str
    additions: int
    deletions: int
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    status: FileStatus = FileStatus.MODIFIED
    old_filename: str | None = None  # For renamed files

    def llm_str(self):
        """
        Returns a string representation of the file change suitable for LLM processing.
        """
        return f"File: {self.filename}, Added lines: +{self.additions}, Removed lines: -{self.deletions}"


@dataclass
class GitDiffResult:
    """Result of a git diff operation with categorized file changes."""

    added_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    renamed_files: list[tuple[str, str]] = field(default_factory=list)  # (old_path, new_path)
    all_changes: list[FileChange] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """Check if there are any changes."""
        return bool(self.added_files or self.modified_files or self.deleted_files or self.renamed_files)

    @property
    def total_files_changed(self) -> int:
        """Total number of files changed."""
        return len(self.added_files) + len(self.modified_files) + len(self.deleted_files) + len(self.renamed_files)


def get_git_diff(repo_dir: Path, version: str) -> list[FileChange]:
    """
    Get the git diff between a specific version and the current working tree (uncommitted changes included).

    :param repo_dir: Path to the repository directory.
    :param version: The commit hash or tag to compare against.
    :return: A list of FileChange objects describing the differences.
    """
    changes: list[FileChange] = []

    try:
        from git import Repo

        repo = Repo(repo_dir)

        # Compare the specified version to the working tree (including staged + unstaged changes)
        diff_index = repo.git.diff(version, "--patch")

        # Group diff by file using parsing logic
        current_file = None
        added: list[str] = []
        removed: list[str] = []
        for line in diff_index.splitlines():
            if line.startswith("diff --git"):
                # Save previous file change, if any
                if current_file:
                    changes.append(current_file)
                # Start a new file
                added, removed = [], []
                filename = line.split(" b/")[-1]
                current_file = FileChange(filename=filename, additions=0, deletions=0)
            elif line.startswith("+++ ") or line.startswith("--- ") or line.startswith("@@"):
                continue
            elif line.startswith("+"):
                added.append(line[1:])
            elif line.startswith("-"):
                removed.append(line[1:])
            if current_file:
                current_file.additions = len(added)
                current_file.deletions = len(removed)
                current_file.added_lines = added
                current_file.removed_lines = removed

        # Append the last file if it exists
        if current_file:
            changes.append(current_file)

    except Exception as e:
        logging.error(f"Error obtaining git diff: {e}")
    return changes


def get_changed_files(repo_dir: Path, old_commit: str, new_commit: str = "HEAD") -> GitDiffResult:
    """
    Get categorized file changes between two commits.

    This function provides a structured view of what files were added, modified,
    deleted, or renamed between two git commits. It's optimized for incremental
    analysis use cases.

    Args:
        repo_dir: Path to the repository directory
        old_commit: The base commit hash to compare from
        new_commit: The target commit hash to compare to (default: HEAD)

    Returns:
        GitDiffResult with categorized file changes
    """
    result = GitDiffResult()

    try:
        from git import Repo

        repo = Repo(repo_dir)

        # Use --name-status to get file status efficiently
        # Format: A (added), M (modified), D (deleted), R### (renamed with similarity)
        diff_output = repo.git.diff(old_commit, new_commit, "--name-status", "-M")

        for line in diff_output.splitlines():
            if not line.strip():
                continue

            parts = line.split("\t")
            if len(parts) < 2:
                continue

            status_code = parts[0]
            filename = parts[1] if len(parts) == 2 else parts[2]  # Renamed files have old\tnew

            if status_code == "A":
                result.added_files.append(filename)
            elif status_code == "M":
                result.modified_files.append(filename)
            elif status_code == "D":
                result.deleted_files.append(parts[1])
            elif status_code.startswith("R"):
                # Renamed file: R100 means 100% similarity (pure rename)
                old_name = parts[1]
                new_name = parts[2]
                result.renamed_files.append((old_name, new_name))

        # Also get detailed changes for context
        result.all_changes = get_git_diff(repo_dir, old_commit)

    except Exception as e:
        logger.error(f"Error getting changed files: {e}")

    return result


def get_file_content_at_commit(repo_dir: Path, file_path: str, commit: str) -> str | None:
    """
    Get the content of a file at a specific commit.

    Args:
        repo_dir: Path to the repository directory
        file_path: Path to the file relative to repo root
        commit: The commit hash to retrieve the file from

    Returns:
        File content as string, or None if file doesn't exist at that commit
    """
    try:
        from git import Repo

        repo = Repo(repo_dir)
        return repo.git.show(f"{commit}:{file_path}")
    except Exception as e:
        logger.debug(f"Could not get content of {file_path} at {commit}: {e}")
        return None


def get_current_commit(repo_dir: Path) -> str | None:
    """
    Get the current HEAD commit hash.

    Args:
        repo_dir: Path to the repository directory

    Returns:
        Commit hash string, or None if not a git repository
    """
    try:
        from git import Repo

        repo = Repo(repo_dir)
        return repo.head.commit.hexsha
    except Exception as e:
        logger.error(f"Error getting current commit: {e}")
        return None
