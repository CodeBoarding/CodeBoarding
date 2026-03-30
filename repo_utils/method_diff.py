"""
Method-level diff analysis.

Cross-references git diff line ranges with MethodEntry positions to determine
which methods were added, modified, or deleted. Populates the ``status`` field
on MethodEntry and the ``file_status`` field on FileMethodGroup.
"""

import logging
import re
import subprocess
from pathlib import Path

from agents.agent_responses import FileEntry, MethodEntry
from agents.change_status import ChangeStatus
from repo_utils.change_detector import ChangeSet

logger = logging.getLogger(__name__)


def _parse_hunk_side(side: str) -> tuple[int, int]:
    """Parse one hunk side like ``+12,3`` or ``-8`` into (start, count)."""
    m = re.match(r"^[+-](\d+)(?:,(\d+))?$", side)
    if m is None:
        return 0, 0
    start = int(m.group(1))
    count = int(m.group(2) or "1")
    return start, count


def _parse_diff_hunks(repo_dir: Path, base_ref: str, file_path: str) -> list[tuple[int, int, int, int]]:
    """Return parsed hunk tuples: (old_start, old_count, new_start, new_count).

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

    hunks: list[tuple[int, int, int, int]] = []
    for line in result.stdout.splitlines():
        if not line.startswith("@@"):
            continue
        # Parse header like: @@ -3,2 +4,5 @@
        parts = line.split()
        if len(parts) < 3:
            continue
        old_start, old_count = _parse_hunk_side(parts[1])
        new_start, new_count = _parse_hunk_side(parts[2])
        if old_start == 0 and new_start == 0:
            continue
        hunks.append((old_start, old_count, new_start, new_count))
    return hunks


def _method_overlaps_ranges(method: MethodEntry, changed_ranges: list[tuple[int, int]]) -> bool:
    """Check if a method's line range overlaps with any changed line range."""
    for start, end in changed_ranges:
        if method.start_line <= end and method.end_line >= start:
            return True
    return False


def _method_fully_inside_ranges(method: MethodEntry, ranges: list[tuple[int, int]]) -> bool:
    """Return True when every line of method is covered by provided ranges."""
    if not ranges:
        return False
    covered = 0
    method_len = method.end_line - method.start_line + 1
    for start, end in ranges:
        overlap_start = max(method.start_line, start)
        overlap_end = min(method.end_line, end)
        if overlap_start <= overlap_end:
            covered += overlap_end - overlap_start + 1
    return covered >= method_len


def _resolve_file_status(file_path: str, changes: ChangeSet) -> ChangeStatus:
    modified_files: set[str] = set(changes.modified_files)
    added_files: set[str] = set(changes.added_files)
    deleted_files: set[str] = set(changes.deleted_files)
    renames: dict[str, str] = changes.renames
    renamed_new_paths: set[str] = set(renames.values())

    if file_path in added_files:
        return ChangeStatus.ADDED
    if file_path in deleted_files:
        return ChangeStatus.DELETED
    if file_path in renamed_new_paths:
        return ChangeStatus.RENAMED
    if file_path in modified_files:
        return ChangeStatus.MODIFIED
    return ChangeStatus.UNCHANGED


def get_method_statuses_for_file(
    methods: list[MethodEntry],
    file_path: str,
    changes: ChangeSet,
    repo_dir: Path,
) -> ChangeStatus:
    """Update method statuses for one file and return the file status."""
    file_status = _resolve_file_status(file_path, changes)

    if file_status == ChangeStatus.ADDED:
        for method in methods:
            method.status = ChangeStatus.ADDED
        return file_status

    if file_status == ChangeStatus.DELETED:
        for method in methods:
            method.status = ChangeStatus.DELETED
        return file_status

    if file_status == ChangeStatus.MODIFIED:
        hunks = _parse_diff_hunks(repo_dir, changes.base_ref, file_path)
        if hunks:
            added_ranges: list[tuple[int, int]] = []
            changed_ranges: list[tuple[int, int]] = []
            deletion_points: list[tuple[int, int]] = []
            for _old_start, old_count, new_start, new_count in hunks:
                if new_count <= 0:
                    # Deletion-only hunk: lines were removed at this point in the
                    # new file.  Any method that spans the deletion point was
                    # modified.  new_start is the line *before* which the deletion
                    # occurred, so the affected region in the new file is the
                    # single line at new_start (the line right after the gap).
                    if new_start > 0:
                        deletion_points.append((new_start, new_start))
                    continue
                new_range = (new_start, new_start + new_count - 1)
                if old_count == 0:
                    added_ranges.append(new_range)
                else:
                    changed_ranges.append(new_range)

            for method in methods:
                if _method_overlaps_ranges(method, changed_ranges):
                    method.status = ChangeStatus.MODIFIED
                elif _method_fully_inside_ranges(method, added_ranges):
                    method.status = ChangeStatus.ADDED
                elif _method_overlaps_ranges(method, added_ranges):
                    method.status = ChangeStatus.MODIFIED
                elif _method_overlaps_ranges(method, deletion_points):
                    method.status = ChangeStatus.MODIFIED
                else:
                    method.status = ChangeStatus.UNCHANGED
        else:
            # No hunks parsed at all (e.g. binary file or diff failure).
            # Mark all methods as unchanged since we cannot determine status.
            for method in methods:
                method.status = ChangeStatus.UNCHANGED
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
