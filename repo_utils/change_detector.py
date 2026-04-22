"""Rename-aware change detection built on top of a parsed git diff.

Exposes :class:`ChangeSet` — a view over ``ParsedGitDiff.files`` with
analysis-friendly accessors (renames, added/modified/deleted file lists,
structural-change predicates) — plus :func:`get_current_commit`.
"""

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from repo_utils.parsed_diff import ChangeType, ParsedDiffFile, ParsedGitDiff

logger = logging.getLogger(__name__)


@dataclass
class ChangeSet:
    """Collection of diff entries with helper accessors."""

    changes: list[ParsedDiffFile] = field(default_factory=list)
    base_ref: str = ""
    target_ref: str = "HEAD"

    @property
    def renames(self) -> dict[str, str]:
        """old_path -> new_path for rename entries."""
        return {c.old_path: c.file_path for c in self.changes if c.is_rename() and c.old_path}

    @property
    def modified_files(self) -> list[str]:
        return [c.file_path for c in self.changes if c.change_type == ChangeType.MODIFIED]

    @property
    def added_files(self) -> list[str]:
        return [c.file_path for c in self.changes if c.change_type == ChangeType.ADDED]

    @property
    def deleted_files(self) -> list[str]:
        return [c.file_path for c in self.changes if c.change_type == ChangeType.DELETED]

    @property
    def all_affected_files(self) -> set[str]:
        return {c.file_path for c in self.changes}

    @property
    def all_old_paths(self) -> set[str]:
        return {c.old_path for c in self.changes if c.old_path}

    def is_empty(self) -> bool:
        return len(self.changes) == 0

    def has_structural_changes(self) -> bool:
        return any(c.is_structural() for c in self.changes)

    def has_only_renames(self) -> bool:
        return len(self.changes) > 0 and all(c.is_rename() for c in self.changes)

    def to_dict(self) -> dict[str, object]:
        return {
            "changes": [
                {
                    "change_type": c.status_code,
                    "file_path": c.file_path,
                    "old_path": c.old_path,
                    "similarity": c.similarity,
                }
                for c in self.changes
            ],
            "base_ref": self.base_ref,
            "target_ref": self.target_ref,
        }


def detect_changes_from_parsed_diff(parsed_diff: ParsedGitDiff) -> ChangeSet:
    """Wrap a ``ParsedGitDiff`` as a ``ChangeSet`` for analysis-side access.

    Path exclusion already happens inside ``get_parsed_git_diff`` via git
    pathspecs, so no further filtering is needed here.
    """
    return ChangeSet(
        changes=list(parsed_diff.files),
        base_ref=parsed_diff.base_ref,
        target_ref=parsed_diff.target_ref,
    )


def get_current_commit(repo_dir: Path) -> str | None:
    """Get the current HEAD commit hash, or None if git fails."""
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
