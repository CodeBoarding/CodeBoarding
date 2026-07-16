"""Primitive git subprocess operations.

Thin subprocess wrappers callable from any layer. Kept as free functions (rather
than a class wrapping ``repo_path``) so callers don't have to thread an instance
around for a handful of calls. The static-analysis LSP-cache warm-start uses
``get_changed_files_since`` to scope re-LSPing to changed files.

Contract: functions here **raise** ``subprocess.CalledProcessError`` /
``FileNotFoundError`` on failure. Callers that want a soft-fail variant
wrap the raising form (see :func:`get_current_commit` vs
:func:`require_current_commit`). Retry / fetch policy lives in callers,
not here, because "what to do when a ref is missing" is use-case-specific.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Decode all git text output as UTF-8 with replacement, not the platform default.
# Why: ``subprocess.run(..., text=True)`` without an explicit encoding decodes
# stdout/stderr through ``locale.getpreferredencoding()`` — on Windows that's
# typically cp1252 and raises ``UnicodeDecodeError`` on UTF-8 bytes git emits
# for non-ASCII paths or translated error messages.
_GIT_TEXT_KWARGS: dict[str, Any] = {"text": True, "encoding": "utf-8", "errors": "replace"}


def _git_argv(*args: str) -> list[str]:
    """Build a git argv with config flags that make output bytes deterministic.

    ``core.quotepath=false`` keeps non-ASCII paths unquoted (so they match the
    live worktree byte-for-byte). Without it, git C-quotes any byte >= 0x80 —
    e.g. ``é.py`` comes back as ``"\\303\\251.py"`` and never matches the file
    on disk, silently mis-scoping any diff-driven flow.
    """
    return ["git", "-c", "core.quotepath=false", *args]


def get_current_commit(repo_dir: Path) -> str | None:
    """Return the current HEAD commit hash, or ``None`` if git fails.

    Non-raising variant used by callers that tolerate a missing commit
    (dirty worktrees, fresh repos, non-git directories). Raising callers
    should use :func:`require_current_commit`.
    """
    try:
        result = subprocess.run(
            _git_argv("rev-parse", "HEAD"),
            cwd=repo_dir,
            capture_output=True,
            **_GIT_TEXT_KWARGS,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def require_current_commit(repo_dir: Path) -> str:
    """Return the current HEAD commit hash, raising on failure."""
    result = subprocess.run(
        _git_argv("rev-parse", "HEAD"),
        cwd=repo_dir,
        capture_output=True,
        **_GIT_TEXT_KWARGS,
        check=True,
    )
    return result.stdout.strip()


def is_git_repository(repo_dir: Path) -> bool:
    """True iff *repo_dir* is inside a git work tree."""
    try:
        subprocess.run(
            _git_argv("rev-parse", "--git-dir"),
            cwd=repo_dir,
            capture_output=True,
            **_GIT_TEXT_KWARGS,
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
            _git_argv("diff", "--cached", "--name-only"),
            _git_argv("diff", "--name-only"),
            _git_argv("ls-files", "--others", "--exclude-standard"),
        ):
            result = subprocess.run(args, cwd=repo_dir, capture_output=True, **_GIT_TEXT_KWARGS, check=True)
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
        _git_argv("diff", "--name-status", "-z", "-M", "-C", from_commit, "HEAD"),
        cwd=repo_dir,
        capture_output=True,
        **_GIT_TEXT_KWARGS,
        check=True,
    )
    changed.update(_parse_name_status_paths(result.stdout, repo_dir))

    changed.update(_list_uncommitted_changed_files(repo_dir))
    logger.info("Found %d changed files since commit %s", len(changed), from_commit)
    return changed


def approve_https_credentials(*, host: str, username: str, password: str, protocol: str = "https") -> None:
    """Store HTTPS credentials via ``git credential approve``.

    Fire-and-forget: caller is responsible for handling failures if needed.
    """
    cred = f"protocol={protocol}\nhost={host}\nusername={username}\npassword={password}\n\n"
    subprocess.run(_git_argv("credential", "approve"), input=cred, **_GIT_TEXT_KWARGS, check=True)


def _list_uncommitted_changed_files(repo_dir: Path) -> set[Path]:
    """Absolute paths of files with staged / unstaged / untracked changes.

    Deleted paths are retained so cache invalidation can remove stale nodes.
    Returns an empty set on any git failure.
    """
    paths: set[Path] = set()
    for args in (
        _git_argv("diff", "--cached", "--name-status", "-z", "-M", "-C"),
        _git_argv("diff", "--name-status", "-z", "-M", "-C"),
        _git_argv("ls-files", "--others", "--exclude-standard", "-z"),
    ):
        try:
            result = subprocess.run(args, cwd=repo_dir, capture_output=True, **_GIT_TEXT_KWARGS, check=True)
        except subprocess.CalledProcessError as exc:
            logger.warning("Failed to list uncommitted changes (%s): %s", args, exc)
            return paths
        if "diff" in args:
            paths.update(_parse_name_status_paths(result.stdout, repo_dir))
            continue
        for line in result.stdout.split("\0"):
            if line:
                paths.add(repo_dir / line)
    return paths


def _parse_name_status_paths(output: str, repo_dir: Path) -> set[Path]:
    """Parse ``git diff --name-status -z`` output into absolute changed paths."""
    paths: set[Path] = set()
    fields = [field for field in output.split("\0") if field]
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        path_count = 2 if status[:1] in {"R", "C"} else 1
        for _ in range(path_count):
            if index >= len(fields):
                return paths
            paths.add(repo_dir / fields[index])
            index += 1
    return paths
