"""Shared git diff parsing for incremental analysis.

Loads a single rename-aware patch diff and exposes both file-level metadata and
parsed hunk content for downstream consumers.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_HUNK_SIDE_RE = re.compile(r"^[+-](\d+)(?:,(\d+))?$")


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
            if finalized is not None:
                files.append(finalized)

            current_file = _parse_raw_line(line)
            patch_lines = []
            continue

        if current_file is not None:
            patch_lines.append(line)

    finalized = _finalize_file_diff(current_file, patch_lines)
    if finalized is not None:
        files.append(finalized)

    return ParsedGitDiff(base_ref=base_ref, target_ref=target_ref, files=files)


def load_parsed_git_diff(
    repo_dir: Path,
    base_ref: str,
    target_ref: str = "HEAD",
    context_lines: int = 3,
    fetch_missing_refs: bool = True,
    exclude_patterns: list[str] | None = None,
) -> ParsedGitDiff:
    """Load a parsed diff with both raw status data and patch hunks."""
    target_ref_value = target_ref
    if exclude_patterns is None:
        exclude_patterns = [".codeboarding/"]
    cmd = [
        "git",
        "diff",
        "--raw",
        f"-U{context_lines}",
        "-M",
        "-C",
        "--find-renames=50%",
        base_ref,
    ]
    if target_ref:
        cmd.append(target_ref)
    if exclude_patterns:
        cmd.extend(["--", "."])
        cmd.extend(f":!{pattern}" for pattern in exclude_patterns)

    def _run_diff() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )

    try:
        result = _run_diff()
    except subprocess.CalledProcessError as exc:
        stderr_lower = (exc.stderr or "").lower()
        if fetch_missing_refs and "bad object" in stderr_lower:
            logger.warning(
                "Git diff failed due to missing ref (%s); fetching refs and retrying once", exc.stderr.strip()
            )
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
                    target_ref=target_ref_value,
                    error=(fetch_err.stderr or str(fetch_err)).strip(),
                )
        else:
            logger.error("Git diff failed: %s", exc.stderr)
            return ParsedGitDiff(
                base_ref=base_ref,
                target_ref=target_ref_value,
                error=(exc.stderr or str(exc)).strip(),
            )
    except FileNotFoundError:
        logger.error("Git not found in PATH")
        return ParsedGitDiff(base_ref=base_ref, target_ref=target_ref_value, error="Git not found in PATH")

    parsed = _parse_diff_output(result.stdout, base_ref, target_ref_value)

    if not target_ref_value:
        _append_untracked_files(parsed, repo_dir, exclude_patterns)

    return parsed


def _append_untracked_files(
    parsed: ParsedGitDiff,
    repo_dir: Path,
    exclude_patterns: list[str],
) -> None:
    """Inject untracked worktree files as ADDED entries.

    `git diff` does not list files unknown to the index, so a user editing
    against the current worktree would otherwise get ``no_changes`` for a
    freshly created file until they ``git add`` it. Only applied for
    worktree diffs (empty target_ref).
    """
    cmd = ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", "."]
    cmd.extend(f":!{pattern}" for pattern in exclude_patterns)
    try:
        result = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        stderr = getattr(exc, "stderr", "") or str(exc)
        logger.warning("Could not enumerate untracked files: %s", stderr.strip() if stderr else exc)
        return

    existing = {file_diff.file_path for file_diff in parsed.files}
    for path in result.stdout.split("\0"):
        if not path or path in existing:
            continue
        parsed.files.append(ParsedDiffFile(status_code="A", file_path=path))
        existing.add(path)


def classify_new_file_ranges(
    hunks: list[ParsedDiffHunk],
) -> tuple[list[tuple[int, int]], list[tuple[int, int]], list[tuple[int, int]]]:
    """Return exact new-file line ranges for additions, modifications, and deletions."""
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
