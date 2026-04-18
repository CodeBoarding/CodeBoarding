"""
Method-level diff analysis.

Cross-references git diff line ranges with ``MethodEntry`` positions to determine
which methods were added, modified, or deleted. Pure functions: callers receive
the computed statuses and decide how to store them.
"""

import logging
import re
import subprocess
from pathlib import Path

from agents.agent_responses import MethodEntry
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
    repo_dir: Path,
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
        hunks = _parse_diff_hunks(repo_dir, changes.base_ref, file_path)
        if not hunks:
            # Binary file or diff failure — cannot determine per-method status.
            for method in methods:
                method_statuses[method.qualified_name] = ChangeStatus.UNCHANGED
            return method_statuses

        added_ranges: list[tuple[int, int]] = []
        changed_ranges: list[tuple[int, int]] = []
        deletion_points: list[tuple[int, int]] = []
        for _old_start, old_count, new_start, new_count in hunks:
            if new_count <= 0:
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
