"""Shared git diff parsing for incremental analysis.

Runs a single rename-aware `git diff` and exposes both file-level metadata and
parsed hunk content for downstream consumers.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from static_analyzer.constants import SOURCE_EXTENSION_TO_LANGUAGE

logger = logging.getLogger(__name__)

_HUNK_SIDE_RE = re.compile(r"^[+-](\d+)(?:,(\d+))?$")
_DIFF_CONTEXT_LINES = 3
_EXCLUDE_PATTERNS: tuple[str, ...] = (".codeboarding/",)


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
    def from_status_code(cls, code: str) -> "ChangeType":
        try:
            return cls(code.upper())
        except ValueError:
            return cls.UNKNOWN


@dataclass
class ParsedDiffHunk:
    """One unified-diff hunk with its body lines."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)


@dataclass
class ParsedDiffFile:
    """Parsed diff data for one file."""

    status_code: str
    file_path: str
    old_path: str | None = None
    similarity: int | None = None
    patch_text: str = ""
    hunks: list[ParsedDiffHunk] = field(default_factory=list)

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


@dataclass
class ParsedGitDiff:
    """Container for a parsed `git diff` invocation."""

    base_ref: str
    target_ref: str
    files: list[ParsedDiffFile] = field(default_factory=list)
    error: str | None = None

    def get_file(self, file_path: str) -> ParsedDiffFile | None:
        for file_diff in self.files:
            if file_diff.file_path == file_path:
                return file_diff
        return None


def _is_source_path(path: str) -> bool:
    """Return True if *path* has an extension CodeBoarding analyzes."""
    return Path(path).suffix.lower() in SOURCE_EXTENSION_TO_LANGUAGE


def _file_is_relevant(file_diff: ParsedDiffFile) -> bool:
    """Return True if *file_diff* references a source file on either side of a rename."""
    if _is_source_path(file_diff.file_path):
        return True
    if file_diff.old_path and _is_source_path(file_diff.old_path):
        return True
    return False


def _parse_hunk_side(side: str) -> tuple[int, int]:
    """Parse one hunk side like ``+12,3`` or ``-8`` into ``(start, count)``."""
    match = _HUNK_SIDE_RE.match(side)
    if match is None:
        return 0, 0
    start = int(match.group(1))
    count = int(match.group(2) or "1")
    return start, count


def _parse_raw_line(line: str) -> ParsedDiffFile | None:
    if not line.startswith(":"):
        return None

    parts = line.split("\t")
    if len(parts) < 2:
        return None

    meta = parts[0].split()
    if len(meta) < 5:
        return None

    status = meta[4]
    status_code = status[0].upper()
    similarity = None
    if len(status) > 1 and status[1:].isdigit():
        similarity = int(status[1:])

    if status_code in {"R", "C"}:
        if len(parts) < 3:
            return None
        return ParsedDiffFile(
            status_code=status_code,
            file_path=parts[2],
            old_path=parts[1],
            similarity=similarity,
        )

    return ParsedDiffFile(
        status_code=status_code,
        file_path=parts[1],
        similarity=similarity,
    )


def _parse_patch_text(patch_text: str) -> list[ParsedDiffHunk]:
    hunks: list[ParsedDiffHunk] = []
    current_hunk: ParsedDiffHunk | None = None

    for line in patch_text.splitlines():
        if line.startswith("@@"):
            parts = line.split()
            if len(parts) < 3:
                continue
            old_start, old_count = _parse_hunk_side(parts[1])
            new_start, new_count = _parse_hunk_side(parts[2])
            if old_start == 0 and new_start == 0:
                continue
            current_hunk = ParsedDiffHunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
            )
            hunks.append(current_hunk)
            continue

        if current_hunk is not None:
            current_hunk.lines.append(line)

    return hunks


def _finalize_file_diff(file_diff: ParsedDiffFile | None, patch_lines: list[str]) -> ParsedDiffFile | None:
    if file_diff is None:
        return None

    file_diff.patch_text = "\n".join(patch_lines).strip()
    file_diff.hunks = _parse_patch_text(file_diff.patch_text)
    return file_diff


def _parse_diff_output(output: str, base_ref: str, target_ref: str) -> ParsedGitDiff:
    files: list[ParsedDiffFile] = []
    current_file: ParsedDiffFile | None = None
    patch_lines: list[str] = []

    for line in output.splitlines():
        if line.startswith(":"):
            finalized = _finalize_file_diff(current_file, patch_lines)
            if finalized is not None and _file_is_relevant(finalized):
                files.append(finalized)

            current_file = _parse_raw_line(line)
            patch_lines = []
            continue

        if current_file is not None:
            patch_lines.append(line)

    finalized = _finalize_file_diff(current_file, patch_lines)
    if finalized is not None and _file_is_relevant(finalized):
        files.append(finalized)

    return ParsedGitDiff(base_ref=base_ref, target_ref=target_ref, files=files)


def get_parsed_git_diff(
    repo_dir: Path,
    base_ref: str,
    target_ref: str = "HEAD",
) -> ParsedGitDiff:
    """Run ``git diff`` and return parsed file-level metadata plus hunk content.

    On a ``bad object`` error for the base ref, fetches all refs once and retries.
    """
    cmd = [
        "git",
        "diff",
        "--raw",
        f"-U{_DIFF_CONTEXT_LINES}",
        "-M",
        "-C",
        "--find-renames=50%",
        base_ref,
    ]
    if target_ref:
        cmd.append(target_ref)
    cmd.extend(["--", "."])
    cmd.extend(f":!{pattern}" for pattern in _EXCLUDE_PATTERNS)

    def _run_diff() -> subprocess.CompletedProcess[str]:
        return subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True, check=True)

    try:
        result = _run_diff()
    except subprocess.CalledProcessError as exc:
        stderr_lower = (exc.stderr or "").lower()
        if "bad object" not in stderr_lower:
            logger.error("Git diff failed: %s", exc.stderr)
            return ParsedGitDiff(base_ref=base_ref, target_ref=target_ref, error=(exc.stderr or str(exc)).strip())

        logger.warning("Git diff failed due to missing ref (%s); fetching refs and retrying once", exc.stderr.strip())
        try:
            subprocess.run(
                ["git", "fetch", "--all", "--prune", "--tags"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=True,
            )
            result = _run_diff()
        except subprocess.CalledProcessError as fetch_err:
            logger.error("Git fetch/diff retry failed: %s", fetch_err.stderr)
            return ParsedGitDiff(
                base_ref=base_ref,
                target_ref=target_ref,
                error=(fetch_err.stderr or str(fetch_err)).strip(),
            )
    except FileNotFoundError:
        logger.error("Git not found in PATH")
        return ParsedGitDiff(base_ref=base_ref, target_ref=target_ref, error="Git not found in PATH")

    parsed = _parse_diff_output(result.stdout, base_ref, target_ref)

    if not target_ref:
        _append_untracked_files(parsed, repo_dir)

    return parsed


def _append_untracked_files(parsed: ParsedGitDiff, repo_dir: Path) -> None:
    """Inject untracked worktree files as ADDED entries.

    `git diff` does not list files unknown to the index, so a user editing
    against the current worktree would otherwise get ``no_changes`` for a
    freshly created file until they ``git add`` it. Only applied for
    worktree diffs (empty target_ref). Entries without a source extension
    that CodeBoarding analyzes are filtered out, matching ``_parse_diff_output``.
    """
    cmd = ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", "."]
    cmd.extend(f":!{pattern}" for pattern in _EXCLUDE_PATTERNS)
    try:
        result = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        stderr = getattr(exc, "stderr", "") or str(exc)
        logger.warning("Could not enumerate untracked files: %s", stderr.strip() if stderr else exc)
        return

    existing = {file_diff.file_path for file_diff in parsed.files}
    for path in result.stdout.split("\0"):
        if not path or path in existing or not _is_source_path(path):
            continue
        parsed.files.append(ParsedDiffFile(status_code="A", file_path=path))
        existing.add(path)


def classify_hunk_ranges(
    hunks: list[ParsedDiffHunk],
) -> tuple[list[tuple[int, int]], list[tuple[int, int]], list[tuple[int, int]]]:
    """Walk hunk bodies and return exact new-file ranges: (added, changed, deletion_points).

    Why: header counts (``new_count``) include context lines around changes, so a single
    hunk spanning two change islands over-reports the touched range. Walking +/-/space
    lines flushes a segment on every context line, yielding the true changed ranges.
    """
    added_ranges: list[tuple[int, int]] = []
    changed_ranges: list[tuple[int, int]] = []
    deletion_points: list[tuple[int, int]] = []

    for hunk in hunks:
        new_line = hunk.new_start
        segment_new_start: int | None = None
        plus_count = 0
        minus_count = 0

        def _flush_segment() -> None:
            nonlocal segment_new_start, plus_count, minus_count

            if plus_count and segment_new_start is not None:
                new_range = (segment_new_start, segment_new_start + plus_count - 1)
                if minus_count:
                    changed_ranges.append(new_range)
                else:
                    added_ranges.append(new_range)
            elif minus_count and new_line > 0:
                deletion_points.append((new_line, new_line))

            segment_new_start = None
            plus_count = 0
            minus_count = 0

        for line in hunk.lines:
            if not line:
                _flush_segment()
                continue

            prefix = line[0]
            if prefix == " ":
                _flush_segment()
                new_line += 1
            elif prefix == "-":
                minus_count += 1
            elif prefix == "+":
                if segment_new_start is None:
                    segment_new_start = new_line
                plus_count += 1
                new_line += 1
            elif prefix == "\\":
                continue
            else:
                _flush_segment()

        _flush_segment()

    return added_ranges, changed_ranges, deletion_points
