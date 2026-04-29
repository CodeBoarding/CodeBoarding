"""Git diff -> :class:`ChangeSet` parser.

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


_DIFF_HEADER_RE = re.compile(r'^diff --git "?a/(.+?)"? "?b/(.+?)"?$')


def _split_patch_bodies(lines: list[str]) -> dict[str, list[str]]:
    """Index patch bodies by their target path.

    ``git diff --raw -U<n>`` emits all ``:``-headers first, then a sequence of
    ``diff --git a/<old> b/<new>`` blocks. Group lines by the new path so
    callers can attach each patch back to the matching :class:`FileChange`.
    Renames and copies are keyed by the new path (matching how raw lines are
    parsed in :func:`_parse_raw_line`).
    """
    bodies: dict[str, list[str]] = {}
    current_path: str | None = None
    current_lines: list[str] = []

    def _flush() -> None:
        if current_path is not None:
            bodies[current_path] = current_lines

    for line in lines:
        match = _DIFF_HEADER_RE.match(line)
        if match is not None:
            _flush()
            current_path = match.group(2)
            current_lines = [line]
            continue
        if current_path is not None:
            current_lines.append(line)

    _flush()
    return bodies


def _parse_diff_output(output: str, base_ref: str, target_ref: str) -> ChangeSet:
    """Parse ``git diff --raw -U<n>`` into a :class:`ChangeSet`.

    The format is two concatenated sections:

    1. A run of ``:``-headers — one per changed file, in order.
    2. A run of ``diff --git a/... b/...`` blocks containing each file's
       unified-diff body, in the same order.

    We walk the output once: the leading ``:``-headers populate ``FileChange``
    entries; the trailing ``diff --git`` blocks are indexed by path and
    attached as patch bodies. This is robust to multi-file diffs where the
    older "header followed by body" parsing accidentally lumped every body
    under the last raw header.
    """
    raw_files: list[FileChange] = []
    body_lines: list[str] = []
    seen_diff_header = False

    for line in output.splitlines():
        if not seen_diff_header and line.startswith(":"):
            parsed = _parse_raw_line(line)
            if parsed is not None:
                raw_files.append(parsed)
            continue
        if line.startswith("diff --git "):
            seen_diff_header = True
        if seen_diff_header:
            body_lines.append(line)

    bodies = _split_patch_bodies(body_lines)

    files: list[FileChange] = []
    for raw in raw_files:
        patch_lines = bodies.get(raw.file_path, [])
        finalized = _finalize_file_diff(raw, patch_lines)
        if _file_is_relevant(finalized):
            files.append(finalized)

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
