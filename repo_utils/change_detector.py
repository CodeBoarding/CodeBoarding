"""
Rename-aware change detection using git.

This module provides enhanced change detection that properly tracks:
- File renames (with similarity percentage)
- File modifications
- Added/deleted files
- Copy detection

Uses `git diff --name-status -M -C` for accurate rename tracking.
"""

import logging
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class ChangeType(Enum):
    """Git change status types."""

    ADDED = "A"
    COPIED = "C"
    DELETED = "D"
    MODIFIED = "M"
    RENAMED = "R"
    TYPE_CHANGED = "T"
    UNMERGED = "U"
    UNKNOWN = "X"


@dataclass
class DetectedChange:
    """A detected file change with rename tracking."""

    change_type: ChangeType
    file_path: str  # Current/new path
    old_path: str | None = None  # For renames/copies: the original path
    similarity: int | None = None  # For renames/copies: 0-100%

    def is_rename(self) -> bool:
        return self.change_type == ChangeType.RENAMED

    def is_content_change(self) -> bool:
        """Returns True if file content was modified (not just metadata/rename)."""
        return self.change_type in (ChangeType.MODIFIED, ChangeType.ADDED)

    def is_structural(self) -> bool:
        """Returns True if this affects file existence (add/delete)."""
        return self.change_type in (ChangeType.ADDED, ChangeType.DELETED)


@dataclass
class ChangeSet:
    """Collection of detected changes with helper methods."""

    changes: list[DetectedChange] = field(default_factory=list)
    base_ref: str = ""
    target_ref: str = "HEAD"

    @property
    def renames(self) -> dict[str, str]:
        """Get rename mapping: old_path -> new_path."""
        return {c.old_path: c.file_path for c in self.changes if c.is_rename() and c.old_path}

    @property
    def modified_files(self) -> list[str]:
        """Get list of modified file paths."""
        return [c.file_path for c in self.changes if c.change_type == ChangeType.MODIFIED]

    @property
    def added_files(self) -> list[str]:
        """Get list of added file paths."""
        return [c.file_path for c in self.changes if c.change_type == ChangeType.ADDED]

    @property
    def deleted_files(self) -> list[str]:
        """Get list of deleted file paths."""
        return [c.file_path for c in self.changes if c.change_type == ChangeType.DELETED]

    @property
    def all_affected_files(self) -> set[str]:
        """Get all files that were affected (current paths)."""
        return {c.file_path for c in self.changes}

    @property
    def all_old_paths(self) -> set[str]:
        """Get all old paths (for renames)."""
        return {c.old_path for c in self.changes if c.old_path}

    def is_empty(self) -> bool:
        return len(self.changes) == 0

    def has_structural_changes(self) -> bool:
        """Returns True if any files were added or deleted."""
        return any(c.is_structural() for c in self.changes)

    def has_only_renames(self) -> bool:
        """Returns True if all changes are pure renames."""
        return all(c.is_rename() for c in self.changes) and len(self.changes) > 0


def detect_changes(repo_dir: Path, base_ref: str, target_ref: str = "HEAD") -> ChangeSet:
    """
    Detect file changes between two refs using rename-aware git diff.

    Args:
        repo_dir: Path to the git repository
        base_ref: Base reference (commit hash, tag, or branch)
        target_ref: Target reference (default: HEAD, includes working tree)

    Returns:
        ChangeSet with all detected changes

    Example:
        changes = detect_changes(repo_path, "abc1234")
        for rename_old, rename_new in changes.renames.items():
            print(f"Renamed: {rename_old} -> {rename_new}")
    """
    changes: list[DetectedChange] = []

    try:
        # Use --name-status for status letters
        # -M: detect renames
        # -C: detect copies
        # --find-renames=50%: consider rename if 50%+ similar
        cmd = [
            "git",
            "diff",
            "--name-status",
            "-M",
            "-C",
            "--find-renames=50%",
            base_ref,
            target_ref,
        ]

        result = subprocess.run(
            cmd,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )

        for line in result.stdout.strip().split("\n"):
            if not line:
                continue

            change = _parse_status_line(line)
            if change:
                changes.append(change)
                logger.debug(f"Detected change: {change.change_type.name} {change.file_path}")

    except subprocess.CalledProcessError as e:
        logger.error(f"Git diff failed: {e.stderr}")
    except FileNotFoundError:
        logger.error("Git not found in PATH")

    return ChangeSet(changes=changes, base_ref=base_ref, target_ref=target_ref)


def detect_changes_from_commit(repo_dir: Path, base_commit: str) -> ChangeSet:
    """
    Detect changes from a specific commit to current working tree.

    This includes both committed and uncommitted changes.
    """
    return detect_changes(repo_dir, base_commit, "HEAD")


def detect_uncommitted_changes(repo_dir: Path) -> ChangeSet:
    """
    Detect only uncommitted changes (staged + unstaged).
    """
    return detect_changes(repo_dir, "HEAD", "")


def _parse_status_line(line: str) -> DetectedChange | None:
    """
    Parse a git diff --name-status line.

    Format examples:
        M       file.py                     # Modified
        A       newfile.py                  # Added
        D       oldfile.py                  # Deleted
        R100    old.py  new.py              # Renamed (100% similar)
        R075    old.py  new.py              # Renamed (75% similar)
        C100    src.py  copy.py             # Copied

    Returns DetectedChange or None if parsing fails.
    """
    parts = line.split("\t")
    if len(parts) < 2:
        return None

    status = parts[0]
    status_char = status[0].upper()

    try:
        change_type = ChangeType(status_char)
    except ValueError:
        logger.warning(f"Unknown git status: {status_char}")
        return None

    # Handle renames and copies (have similarity percentage and two paths)
    if change_type in (ChangeType.RENAMED, ChangeType.COPIED):
        if len(parts) < 3:
            return None

        # Extract similarity percentage (e.g., "R100" -> 100)
        similarity = None
        if len(status) > 1:
            try:
                similarity = int(status[1:])
            except ValueError:
                pass

        return DetectedChange(
            change_type=change_type,
            file_path=parts[2],  # New path
            old_path=parts[1],  # Old path
            similarity=similarity,
        )

    # Simple change (A, M, D, T)
    return DetectedChange(
        change_type=change_type,
        file_path=parts[1],
    )


def get_current_commit(repo_dir: Path) -> str | None:
    """Get the current HEAD commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None
