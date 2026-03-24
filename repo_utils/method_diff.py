"""
Method-level diff analysis.

Cross-references git diff line ranges with MethodEntry positions to determine
which methods were added, modified, or deleted. Populates the ``status`` field
on MethodEntry and the ``file_status`` field on FileMethodGroup.
"""

import logging
import subprocess
from pathlib import Path

from agents.agent_responses import FileEntry, MethodEntry
from repo_utils.change_detector import ChangeSet

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


def _resolve_file_status(file_path: str, changes: ChangeSet) -> str:
    modified_files: set[str] = set(changes.modified_files)
    added_files: set[str] = set(changes.added_files)
    deleted_files: set[str] = set(changes.deleted_files)
    renames: dict[str, str] = changes.renames
    renamed_new_paths: set[str] = set(renames.values())

    if file_path in added_files:
        return "added"
    if file_path in deleted_files:
        return "deleted"
    if file_path in renamed_new_paths:
        return "renamed"
    if file_path in modified_files:
        return "modified"
    return "unchanged"


def get_method_statuses_for_file(
    methods: list[MethodEntry],
    file_path: str,
    changes: ChangeSet,
    repo_dir: Path,
) -> str:
    """Update method statuses for one file and return the file status."""
    file_status = _resolve_file_status(file_path, changes)

    if file_status == "added":
        for method in methods:
            method.status = "added"
        return file_status

    if file_status == "deleted":
        for method in methods:
            method.status = "deleted"
        return file_status

    if file_status == "modified":
        changed_ranges = _parse_diff_line_ranges(repo_dir, changes.base_ref, file_path)
        if changed_ranges:
            for method in methods:
                method.status = "modified" if _method_overlaps_ranges(method, changed_ranges) else "unchanged"
        else:
            # Zero-context new-file ranges can be empty for deletion-only hunks.
            # Mark all methods as modified as a safe fallback.
            for method in methods:
                method.status = "modified"
        return file_status

    return file_status


def apply_method_diffs_to_file_index(
    files: dict[str, FileEntry],
    changes: ChangeSet,
    repo_dir: Path,
) -> dict[str, FileEntry]:
    """Apply file and method statuses to top-level files index in-place."""
    if changes.is_empty():
        return files

    for file_path, file_entry in files.items():
        file_entry.file_status = get_method_statuses_for_file(file_entry.methods, file_path, changes, repo_dir)

    return files
