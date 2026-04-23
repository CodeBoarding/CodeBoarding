"""Primitive git subprocess operations.

Thin subprocess wrappers callable from any layer. Kept as free functions (rather
than a class wrapping ``repo_path``) so callers don't have to thread an instance
around for a handful of calls. Two groups of callers today:

- the semantic incremental pipeline (``diff_parser``, ``run_metadata``, CLI)
- the static-analysis LSP-cache invalidator (``incremental_orchestrator``)

Contract: functions here **raise** ``subprocess.CalledProcessError`` /
``FileNotFoundError`` on failure. Callers that want a soft-fail variant
wrap the raising form (see :func:`get_current_commit` vs
:func:`require_current_commit`). Retry / fetch policy lives in callers,
not here, because "what to do when a ref is missing" is use-case-specific.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger(__name__)


def get_current_commit(repo_dir: Path) -> str | None:
    """Return the current HEAD commit hash, or ``None`` if git fails.

    Non-raising variant used by callers that tolerate a missing commit
    (dirty worktrees, fresh repos, non-git directories). Raising callers
    should use :func:`require_current_commit`.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def require_current_commit(repo_dir: Path) -> str:
    """Return the current HEAD commit hash, raising on failure."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def is_git_repository(repo_dir: Path) -> bool:
    """True iff *repo_dir* is inside a git work tree."""
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def has_uncommitted_changes(repo_dir: Path) -> bool:
    """True if the index or worktree has any uncommitted / untracked changes.

    Used by the LSP-cache invalidator to decide whether a cache hit at the
    same commit is still safe to reuse.
    """
    try:
        for args in (
            ["git", "diff", "--cached", "--name-only"],
            ["git", "diff", "--name-only"],
            ["git", "ls-files", "--others", "--exclude-standard"],
        ):
            result = subprocess.run(args, cwd=repo_dir, capture_output=True, text=True, check=True)
            if result.stdout.strip():
                return True
        return False
    except subprocess.CalledProcessError as exc:
        logger.warning("Failed to check for uncommitted changes: %s", exc)
        return False


def get_changed_files_since(repo_dir: Path, from_commit: str) -> set[Path]:
    """Return absolute paths of files changed since *from_commit*.

    Includes committed diff plus staged / unstaged / untracked changes. Used
    by the LSP incremental orchestrator for cache invalidation — it needs
    file paths, not hunks, so this is much cheaper than full diff parsing.

    Raises ``subprocess.CalledProcessError`` if the baseline ref is bad.
    """
    changed: set[Path] = set()

    result = subprocess.run(
        ["git", "diff", "--name-only", from_commit, "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stdout.strip().split("\n"):
        if line:
            changed.add(repo_dir / line)

    changed.update(_list_uncommitted_changed_files(repo_dir))
    logger.info("Found %d changed files since commit %s", len(changed), from_commit)
    return changed


def run_raw_diff(
    repo_dir: Path,
    base_ref: str,
    target_ref: str,
    *,
    context_lines: int = 3,
    exclude_patterns: Sequence[str] = (),
) -> str:
    """Run ``git diff --raw -U<n> -M -C --find-renames=50%`` and return stdout.

    Raises ``subprocess.CalledProcessError`` on git failure (including a bad
    baseline ref — callers that want to retry after ``git fetch`` should catch
    and re-invoke). Empty ``target_ref`` diffs the worktree against *base_ref*.
    """
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
    cmd.extend(["--", "."])
    cmd.extend(f":!{pattern}" for pattern in exclude_patterns)

    result = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True, check=True)
    return result.stdout


def fetch_all(repo_dir: Path) -> None:
    """Run ``git fetch --all --prune --tags``; raises on failure."""
    subprocess.run(
        ["git", "fetch", "--all", "--prune", "--tags"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )


def list_untracked_files(repo_dir: Path, *, exclude_patterns: Sequence[str] = ()) -> list[str]:
    """Return repo-relative paths of untracked files respecting ``.gitignore``.

    Uses ``-z`` / NUL-separated output so filenames with embedded newlines
    stay intact. Distinct from :func:`_list_uncommitted_changed_files`, which
    also includes staged/unstaged and returns absolute paths.
    """
    cmd = ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", "."]
    cmd.extend(f":!{pattern}" for pattern in exclude_patterns)
    result = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True, check=True)
    return [path for path in result.stdout.split("\0") if path]


def _list_uncommitted_changed_files(repo_dir: Path) -> set[Path]:
    """Absolute paths of files with staged / unstaged / untracked changes.

    Nonexistent paths are skipped (handles files staged-for-delete then
    popped from the worktree). Returns an empty set on any git failure.
    """
    paths: set[Path] = set()
    for args in (
        ["git", "diff", "--cached", "--name-only"],
        ["git", "diff", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        try:
            result = subprocess.run(args, cwd=repo_dir, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as exc:
            logger.warning("Failed to list uncommitted changes (%s): %s", args, exc)
            return paths
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            candidate = repo_dir / line
            if candidate.exists():
                paths.add(candidate)
    return paths
