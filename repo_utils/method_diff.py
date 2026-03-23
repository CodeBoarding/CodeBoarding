"""
Method-level diff analysis.

Cross-references git diff line ranges with MethodEntry positions to determine
which methods were added, modified, or deleted. Populates the ``status`` field
on MethodEntry and the ``file_status`` field on FileMethodGroup.
"""

import logging
import subprocess
from pathlib import Path

from agents.agent_responses import FileMethodGroup, MethodEntry
from repo_utils.change_detector import ChangeSet, ChangeType

logger = logging.getLogger(__name__)


def _parse_diff_line_ranges(repo_dir: Path, base_ref: str, file_path: str) -> list[tuple[int, int]]:
    """Return list of (start, end) line ranges in the *new* file that were changed.

    Uses ``git diff -U0`` to get exact changed line ranges without context lines.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "-U0", base_ref, "--", file_path],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    ranges: list[tuple[int, int]] = []
    for line in result.stdout.splitlines():
        if not line.startswith("@@"):
            continue
        # Parse @@ -old_start,old_count +new_start,new_count @@
        parts = line.split()
        for part in parts:
            if part.startswith("+") and not part.startswith("+++"):
                # +start or +start,count
                part = part[1:]
                if "," in part:
                    start_str, count_str = part.split(",", 1)
                    start = int(start_str)
                    count = int(count_str)
                else:
                    start = int(part)
                    count = 1
                if count > 0:
                    ranges.append((start, start + count - 1))
                break
    return ranges


def _method_overlaps_ranges(method: MethodEntry, changed_ranges: list[tuple[int, int]]) -> bool:
    """Check if a method's line range overlaps with any changed line range."""
    for start, end in changed_ranges:
        if method.start_line <= end and method.end_line >= start:
            return True
    return False


def apply_method_diffs(
    file_method_groups: list[FileMethodGroup],
    changes: ChangeSet,
    repo_dir: Path,
) -> list[FileMethodGroup]:
    """Apply diff status to FileMethodGroups and their MethodEntries in-place.

    Args:
        file_method_groups: The file-method groups to annotate.
        changes: The ChangeSet from change detection.
        repo_dir: Path to the git repository root.

    Returns:
        The same list, with ``file_status`` and method ``status`` fields populated.
    """
    if changes.is_empty():
        return file_method_groups

    # Build lookup maps from the ChangeSet
    modified_files: set[str] = set(changes.modified_files)
    added_files: set[str] = set(changes.added_files)
    deleted_files: set[str] = set(changes.deleted_files)
    renames: dict[str, str] = changes.renames  # old_path -> new_path
    renamed_new_paths: set[str] = set(renames.values())

    for group in file_method_groups:
        fp = group.file_path

        if fp in added_files:
            group.file_status = "added"
            for method in group.methods:
                method.status = "added"

        elif fp in deleted_files:
            group.file_status = "deleted"
            for method in group.methods:
                method.status = "deleted"

        elif fp in renamed_new_paths:
            group.file_status = "renamed"
            # Methods themselves are unchanged unless the file was also modified

        elif fp in modified_files:
            group.file_status = "modified"
            # Determine which methods overlap with changed line ranges
            changed_ranges = _parse_diff_line_ranges(repo_dir, changes.base_ref, fp)
            if changed_ranges:
                for method in group.methods:
                    if _method_overlaps_ranges(method, changed_ranges):
                        method.status = "modified"

    return file_method_groups
