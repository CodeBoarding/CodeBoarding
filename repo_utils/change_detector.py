"""Domain model for git-diff-derived changes.

Pure data + methods, no I/O. Produced by :func:`repo_utils.diff_parser.detect_changes`,
consumed by the incremental analysis pipeline:

- file-level accessors: ``added_files``, ``modified_files``, ``deleted_files``,
  ``renames``, ``file_status(path)``
- per-file drill-down: ``get_file(path)`` -> :class:`FileChange` with hunks, and
  ``FileChange.classify_method_statuses(methods, prev_methods)`` for method-level status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from agents.agent_responses import MethodEntry
from agents.change_status import ChangeStatus


class ChangeDetectionError(RuntimeError):
    """Raised when git-based change detection cannot produce a trustworthy diff."""


class ChangeType(Enum):
    """Git diff status codes."""

    ADDED = "A"
    COPIED = "C"
    DELETED = "D"
    MODIFIED = "M"
    RENAMED = "R"
    TYPE_CHANGED = "T"
    UNMERGED = "U"
    UNKNOWN = "X"

    @classmethod
    def from_status_code(cls, code: str) -> ChangeType:
        try:
            return cls(code.upper())
        except ValueError:
            return cls.UNKNOWN


@dataclass
class DiffHunk:
    """One unified-diff hunk with its body lines."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)


@dataclass
class ChangedLineRanges:
    """Line-range classification for one file's diff.

    ``added`` and ``changed`` are in new-file coordinates; ``deletions``
    are in **old-file** coordinates (pure-deletion lines don't exist in the
    new file, so there is no correct new-file position for them).
    """

    added: list[tuple[int, int]] = field(default_factory=list)
    changed: list[tuple[int, int]] = field(default_factory=list)
    deletions: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class FileChange:
    """One file's worth of change data from a git diff."""

    status_code: str
    file_path: str
    old_path: str | None = None
    similarity: int | None = None
    patch_text: str = ""
    hunks: list[DiffHunk] = field(default_factory=list)

    @property
    def change_type(self) -> ChangeType:
        return ChangeType.from_status_code(self.status_code)

    def is_rename(self) -> bool:
        return self.change_type == ChangeType.RENAMED

    def is_content_change(self) -> bool:
        """True if file content was modified (not just metadata/rename)."""
        return self.change_type in (ChangeType.MODIFIED, ChangeType.ADDED)

    def is_structural(self) -> bool:
        """True if this affects file existence (add/delete)."""
        return self.change_type in (ChangeType.ADDED, ChangeType.DELETED)

    def changed_line_ranges(self) -> ChangedLineRanges:
        """Classify this file's hunk bodies into added / changed / deletion ranges.

        Why: a hunk header's ``new_count`` includes context lines, so a single
        hunk spanning two change islands over-reports the touched range.
        Walking +/-/space lines flushes a segment on every context line,
        yielding the true changed ranges.

        ``added`` and ``changed`` are in new-file coordinates; ``deletions``
        are in old-file coordinates.  Mixed segments (both ``+`` and ``-``
        lines without an intervening context line) are split: the first
        ``minus_count`` new-file lines become ``changed`` (replacement) and
        any excess become ``added`` (pure addition).
        """
        added: list[tuple[int, int]] = []
        changed: list[tuple[int, int]] = []
        deletions: list[tuple[int, int]] = []

        for hunk in self.hunks:
            new_line = hunk.new_start
            old_line = hunk.old_start
            segment_new_start: int | None = None
            segment_old_start: int | None = None
            plus_count = 0
            minus_count = 0

            def _flush() -> None:
                nonlocal segment_new_start, segment_old_start, plus_count, minus_count
                if plus_count and segment_new_start is not None:
                    if minus_count:
                        replace_count = min(plus_count, minus_count)
                        changed.append((segment_new_start, segment_new_start + replace_count - 1))
                        if plus_count > replace_count:
                            added.append(
                                (
                                    segment_new_start + replace_count,
                                    segment_new_start + plus_count - 1,
                                )
                            )
                        if minus_count > replace_count and segment_old_start is not None:
                            deletions.append(
                                (
                                    segment_old_start + replace_count,
                                    segment_old_start + minus_count - 1,
                                )
                            )
                    else:
                        added.append((segment_new_start, segment_new_start + plus_count - 1))
                elif minus_count and segment_old_start is not None:
                    deletions.append((segment_old_start, segment_old_start + minus_count - 1))
                segment_new_start = None
                segment_old_start = None
                plus_count = 0
                minus_count = 0

            for line in hunk.lines:
                if not line:
                    _flush()
                    continue
                prefix = line[0]
                if prefix == " ":
                    _flush()
                    new_line += 1
                    old_line += 1
                elif prefix == "-":
                    if segment_old_start is None:
                        segment_old_start = old_line
                    minus_count += 1
                    old_line += 1
                elif prefix == "+":
                    if segment_new_start is None:
                        segment_new_start = new_line
                    plus_count += 1
                    new_line += 1
                elif prefix == "\\":
                    continue
                else:
                    _flush()

            _flush()

        return ChangedLineRanges(added=added, changed=changed, deletions=deletions)

    def classify_method_statuses(
        self,
        methods: list[MethodEntry],
        prev_methods: list[MethodEntry] | None = None,
    ) -> dict[str, ChangeStatus]:
        """Per-method ``ChangeStatus`` for *methods*, given this file's hunks.

        Called after :meth:`ChangeSet.file_status` says the file is MODIFIED.
        For ADDED / DELETED files the caller marks all methods wholesale
        without needing hunks.

        *prev_methods*, when provided, carry old-file line numbers and are
        checked against old-file ``deletions`` ranges: a surviving method
        whose previous position overlaps pure-deletion lines is promoted
        from UNCHANGED to MODIFIED.
        """
        if not self.hunks:
            return {m.qualified_name: ChangeStatus.UNCHANGED for m in methods}

        ranges = self.changed_line_ranges()
        statuses: dict[str, ChangeStatus] = {}
        for method in methods:
            if _overlaps(method, ranges.changed):
                statuses[method.qualified_name] = ChangeStatus.MODIFIED
            elif _fully_inside(method, ranges.added):
                statuses[method.qualified_name] = ChangeStatus.ADDED
            elif _overlaps(method, ranges.added):
                statuses[method.qualified_name] = ChangeStatus.MODIFIED
            else:
                statuses[method.qualified_name] = ChangeStatus.UNCHANGED

        if prev_methods and ranges.deletions:
            current_names = {m.qualified_name for m in methods}
            for prev in prev_methods:
                if (
                    prev.qualified_name in current_names
                    and statuses.get(prev.qualified_name) == ChangeStatus.UNCHANGED
                    and _overlaps(prev, ranges.deletions)
                ):
                    statuses[prev.qualified_name] = ChangeStatus.MODIFIED

        return statuses


@dataclass
class ChangeSet:
    """The set of file changes detected between two git refs.

    Built from ``git diff --raw`` output by :func:`repo_utils.diff_parser.detect_changes`.
    Empty ``files`` + non-None ``error`` means the diff invocation failed —
    callers check ``error`` first.
    """

    base_ref: str
    target_ref: str
    files: list[FileChange] = field(default_factory=list)
    error: str | None = None

    def get_file(self, file_path: str) -> FileChange | None:
        for file_diff in self.files:
            if file_diff.file_path == file_path:
                return file_diff
        return None

    def is_empty(self) -> bool:
        return not self.files

    @property
    def added_files(self) -> list[str]:
        return [f.file_path for f in self.files if f.change_type == ChangeType.ADDED]

    @property
    def modified_files(self) -> list[str]:
        return [f.file_path for f in self.files if f.change_type == ChangeType.MODIFIED]

    @property
    def deleted_files(self) -> list[str]:
        return [f.file_path for f in self.files if f.change_type == ChangeType.DELETED]

    @property
    def renames(self) -> dict[str, str]:
        """old_path -> new_path for rename entries."""
        return {f.old_path: f.file_path for f in self.files if f.is_rename() and f.old_path}

    def has_renames_or_copies(self) -> bool:
        return any(f.change_type in (ChangeType.RENAMED, ChangeType.COPIED) for f in self.files)

    def file_status(self, file_path: str) -> ChangeStatus:
        """Return the ``ChangeStatus`` for *file_path*, or UNCHANGED if unknown.

        Why the ``__members__`` lookup: ``ChangeType`` and ``ChangeStatus`` share
        member names (``ADDED``, ``MODIFIED``, ``DELETED``, ``RENAMED``) but have
        disjoint string values (``"A"`` vs ``"added"``). Looking up by ``.name``
        stays correct if either enum grows a new shared member.
        """
        fc = self.get_file(file_path)
        if fc is None:
            return ChangeStatus.UNCHANGED
        return ChangeStatus.__members__.get(fc.change_type.name, ChangeStatus.UNCHANGED)

    def to_dict(self) -> dict[str, object]:
        return {
            "changes": [
                {
                    "change_type": f.status_code,
                    "file_path": f.file_path,
                    "old_path": f.old_path,
                    "similarity": f.similarity,
                }
                for f in self.files
            ],
            "base_ref": self.base_ref,
            "target_ref": self.target_ref,
        }


# ---------------------------------------------------------------------------
# Method-range helpers (used by FileChange.classify_method_statuses)
# ---------------------------------------------------------------------------
def _overlaps(method: MethodEntry, ranges: list[tuple[int, int]]) -> bool:
    for start, end in ranges:
        if method.start_line <= end and method.end_line >= start:
            return True
    return False


def _fully_inside(method: MethodEntry, ranges: list[tuple[int, int]]) -> bool:
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
