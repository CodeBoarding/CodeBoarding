"""
Method-level diff analysis.

Cross-references git diff line ranges with MethodEntry positions to determine
which methods were added, modified, or deleted. Populates the ``status`` field
on MethodEntry and the ``file_status`` field on FileMethodGroup.
"""

import logging
from pathlib import Path

from agents.agent_responses import FileEntry, MethodEntry
from agents.change_status import ChangeStatus
from repo_utils.change_detector import ChangeSet
from repo_utils.parsed_diff import classify_new_file_ranges, ParsedGitDiff

logger = logging.getLogger(__name__)


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
        diff_file = _get_diff_file(changes.parsed_diff, file_path)
        if diff_file is not None and diff_file.hunks:
            added_ranges, changed_ranges, deletion_points = classify_new_file_ranges(diff_file.hunks)
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


def _get_diff_file(parsed_diff: ParsedGitDiff | None, file_path: str):
    if parsed_diff is None:
        return None
    return parsed_diff.get_file(file_path)
