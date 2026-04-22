"""
Method-level diff analysis.

Cross-references git diff line ranges with ``MethodEntry`` positions to determine
which methods were added, modified, or deleted. Consumes the hunks already parsed
by ``repo_utils.parsed_diff`` so we don't re-run ``git diff`` per file.
"""

import logging

from agents.agent_responses import MethodEntry
from agents.change_status import ChangeStatus
from repo_utils.change_detector import ChangeSet
from repo_utils.parsed_diff import ParsedGitDiff, classify_hunk_ranges

logger = logging.getLogger(__name__)


def _method_overlaps_ranges(method: MethodEntry, changed_ranges: list[tuple[int, int]]) -> bool:
    for start, end in changed_ranges:
        if method.start_line <= end and method.end_line >= start:
            return True
    return False


def _method_fully_inside_ranges(method: MethodEntry, ranges: list[tuple[int, int]]) -> bool:
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


def resolve_file_status(file_path: str, changes: ChangeSet) -> ChangeStatus:
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


def compute_method_statuses_for_file(
    methods: list[MethodEntry],
    file_path: str,
    changes: ChangeSet,
    parsed_diff: ParsedGitDiff,
) -> dict[str, ChangeStatus]:
    """Compute per-method statuses for one file.

    Callers that also need the file-level status should call
    :func:`resolve_file_status` directly with the same ``changes``.
    """
    file_status = resolve_file_status(file_path, changes)
    method_statuses: dict[str, ChangeStatus] = {}

    if file_status == ChangeStatus.ADDED:
        for method in methods:
            method_statuses[method.qualified_name] = ChangeStatus.ADDED
        return method_statuses

    if file_status == ChangeStatus.DELETED:
        for method in methods:
            method_statuses[method.qualified_name] = ChangeStatus.DELETED
        return method_statuses

    if file_status == ChangeStatus.MODIFIED:
        file_diff = parsed_diff.get_file(file_path)
        hunks = file_diff.hunks if file_diff else []
        if not hunks:
            # Binary file or missing from the parsed diff — cannot determine per-method status.
            for method in methods:
                method_statuses[method.qualified_name] = ChangeStatus.UNCHANGED
            return method_statuses

        added_ranges, changed_ranges, deletion_points = classify_hunk_ranges(hunks)

        for method in methods:
            if _method_overlaps_ranges(method, changed_ranges):
                method_statuses[method.qualified_name] = ChangeStatus.MODIFIED
            elif _method_fully_inside_ranges(method, added_ranges):
                method_statuses[method.qualified_name] = ChangeStatus.ADDED
            elif _method_overlaps_ranges(method, added_ranges):
                method_statuses[method.qualified_name] = ChangeStatus.MODIFIED
            elif _method_overlaps_ranges(method, deletion_points):
                method_statuses[method.qualified_name] = ChangeStatus.MODIFIED
            else:
                method_statuses[method.qualified_name] = ChangeStatus.UNCHANGED
        return method_statuses

    # UNCHANGED / RENAMED: every method is UNCHANGED relative to the diff.
    for method in methods:
        method_statuses[method.qualified_name] = ChangeStatus.UNCHANGED
    return method_statuses
