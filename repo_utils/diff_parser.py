"""Git diff → :class:`ChangeSet` parser.

Orchestrates the git I/O primitives in :mod:`repo_utils.git_ops` and parses
the raw output into a :class:`ChangeSet`. Non-source entries and the
``.codeboarding/`` directory are filtered via pathspec during the git call
so downstream consumers don't have to re-filter.

I/O boundary: all ``subprocess`` calls live in ``git_ops``. This module
does no direct git work — it composes primitives, maps their exceptions
to ``ChangeSet(error=...)``, and parses the resulting text.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from repo_utils.change_detector import ChangeSet, DiffHunk, FileChange
from repo_utils.git_ops import fetch_all, list_untracked_files, run_raw_diff
from static_analyzer.constants import SOURCE_EXTENSION_TO_LANGUAGE
from utils import CODEBOARDING_DIR_NAME

logger = logging.getLogger(__name__)

_HUNK_SIDE_RE = re.compile(r"^[+-](\d+)(?:,(\d+))?$")
_DIFF_CONTEXT_LINES = 3
_EXCLUDE_PATTERNS: tuple[str, ...] = (f"{CODEBOARDING_DIR_NAME}/",)


def detect_changes(
    repo_dir: Path,
    base_ref: str,
    target_ref: str = "HEAD",
) -> ChangeSet:
    """Run ``git diff`` and return a parsed :class:`ChangeSet`.

    On a ``bad object`` error for the base ref, runs ``git fetch`` once and
    retries — this handles CI shallow clones where the baseline commit isn't
    yet local. All other errors are surfaced via ``ChangeSet.error``.
    """
    try:
        output = _run_diff_with_fetch_retry(repo_dir, base_ref, target_ref)
    except subprocess.CalledProcessError as exc:
        return ChangeSet(base_ref=base_ref, target_ref=target_ref, error=(exc.stderr or str(exc)).strip())
    except FileNotFoundError:
        logger.error("Git not found in PATH")
        return ChangeSet(base_ref=base_ref, target_ref=target_ref, error="Git not found in PATH")

    parsed = _parse_diff_output(output, base_ref, target_ref)

    if not target_ref:
        _append_untracked_files(parsed, repo_dir)

    return parsed


def _run_diff_with_fetch_retry(repo_dir: Path, base_ref: str, target_ref: str) -> str:
    """Run ``git diff``, fetching + retrying once on a missing-ref error."""
    try:
        return run_raw_diff(
            repo_dir,
            base_ref,
            target_ref,
            context_lines=_DIFF_CONTEXT_LINES,
            exclude_patterns=_EXCLUDE_PATTERNS,
        )
    except subprocess.CalledProcessError as exc:
        if "bad object" not in (exc.stderr or "").lower():
            logger.error("Git diff failed: %s", exc.stderr)
            raise
        logger.warning("Git diff failed due to missing ref (%s); fetching refs and retrying once", exc.stderr.strip())
        fetch_all(repo_dir)
        return run_raw_diff(
            repo_dir,
            base_ref,
            target_ref,
            context_lines=_DIFF_CONTEXT_LINES,
            exclude_patterns=_EXCLUDE_PATTERNS,
        )


# ---------------------------------------------------------------------------
# Parsing internals
# ---------------------------------------------------------------------------
def _is_source_path(path: str) -> bool:
    """True if *path* has an extension CodeBoarding analyzes."""
    return Path(path).suffix.lower() in SOURCE_EXTENSION_TO_LANGUAGE


def _file_is_relevant(file_diff: FileChange) -> bool:
    """True if *file_diff* references a source file on either side of a rename."""
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


def _parse_raw_line(line: str) -> FileChange | None:
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
        return FileChange(
            status_code=status_code,
            file_path=parts[2],
            old_path=parts[1],
            similarity=similarity,
        )

    return FileChange(
        status_code=status_code,
        file_path=parts[1],
        similarity=similarity,
    )


def _parse_patch_text(patch_text: str) -> list[DiffHunk]:
    hunks: list[DiffHunk] = []
    current_hunk: DiffHunk | None = None

    for line in patch_text.splitlines():
        if line.startswith("@@"):
            parts = line.split()
            if len(parts) < 3:
                continue
            old_start, old_count = _parse_hunk_side(parts[1])
            new_start, new_count = _parse_hunk_side(parts[2])
            if old_start == 0 and new_start == 0:
                continue
            current_hunk = DiffHunk(
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


def _finalize_file_diff(file_diff: FileChange, patch_lines: list[str]) -> FileChange:
    file_diff.patch_text = "\n".join(patch_lines).strip()
    file_diff.hunks = _parse_patch_text(file_diff.patch_text)
    return file_diff


def _parse_diff_output(output: str, base_ref: str, target_ref: str) -> ChangeSet:
    """Walk ``git diff --raw`` output, grouping each ``:``-header with its patch body.

    The output is a sequence of blocks: each begins with a ``:`` header line
    (parsed into a ``FileChange``) followed by that file's patch text until
    the next header. Lines before the first header are ignored.
    """
    files: list[FileChange] = []
    current_file: FileChange | None = None
    patch_lines: list[str] = []

    def _commit_current() -> None:
        if current_file is None:
            return
        finalized = _finalize_file_diff(current_file, patch_lines)
        if _file_is_relevant(finalized):
            files.append(finalized)

    for line in output.splitlines():
        if line.startswith(":"):
            _commit_current()
            current_file = _parse_raw_line(line)
            patch_lines = []
        elif current_file is not None:
            patch_lines.append(line)

    _commit_current()
    return ChangeSet(base_ref=base_ref, target_ref=target_ref, files=files)


def _append_untracked_files(parsed: ChangeSet, repo_dir: Path) -> None:
    """Inject untracked worktree files as ADDED entries.

    ``git diff`` does not list files unknown to the index, so a user editing
    against the current worktree would otherwise get ``no_changes`` for a
    freshly created file until they ``git add`` it. Only applied for worktree
    diffs (empty target_ref). Entries without a source extension CodeBoarding
    analyzes are filtered out, matching ``_parse_diff_output``.
    """
    try:
        untracked = list_untracked_files(repo_dir, exclude_patterns=_EXCLUDE_PATTERNS)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        stderr = getattr(exc, "stderr", "") or str(exc)
        logger.warning("Could not enumerate untracked files: %s", stderr.strip() if stderr else exc)
        return

    existing = {file_diff.file_path for file_diff in parsed.files}
    for path in untracked:
        if path in existing or not _is_source_path(path):
            continue
        parsed.files.append(FileChange(status_code="A", file_path=path))
        existing.add(path)
