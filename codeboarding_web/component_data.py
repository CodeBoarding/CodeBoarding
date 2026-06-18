"""Helpers for per-component health, git-change, and diff data."""

import json
import logging
import os
import subprocess
from pathlib import Path

from agents.agent_responses import Component
from repo_utils.diff_parser import detect_changes

logger = logging.getLogger(__name__)


def component_files(comp: Component, repo_path: Path) -> set[str]:
    """Return repo-relative source files referenced by *comp*.key_entities.

    Absolute reference_file values are normalised via os.path.relpath. None
    entries are skipped. Paths are forward-slash normalised.
    """
    result: set[str] = set()
    for ref in comp.key_entities:
        f = ref.reference_file
        if not f:
            continue
        if Path(f).is_absolute():
            rel = os.path.relpath(f, repo_path)
        else:
            rel = f
        # Normalize both relative and absolute paths: collapse '.', '..', backslashes
        rel = Path(os.path.normpath(rel)).as_posix()
        result.add(rel)
    return result


def load_warning_counts(output_dir: Path) -> dict[str, int]:
    """Read health_report.json and return {repo_rel_file: warning_findings}.

    Returns {} if the file is absent or unreadable.
    """
    health_path = output_dir / "health" / "health_report.json"
    try:
        raw = json.loads(health_path.read_text(encoding="utf-8"))
        return {entry["file_path"]: entry.get("warning_findings", 0) for entry in raw.get("file_summaries", [])}
    except Exception:
        logger.debug("health_report.json not readable", exc_info=True)
        return {}


def changed_files(repo_path: Path) -> set[str]:
    """Return repo-relative paths of files changed vs HEAD (working tree + untracked).

    Returns set() on error or when repo_path is not a git repository.
    """
    try:
        change_set = detect_changes(repo_path, "HEAD", "")
        if change_set.error:
            logger.debug("detect_changes error: %s", change_set.error)
            return set()
        return {fc.file_path for fc in change_set.files}
    except Exception:
        logger.debug("changed_files failed", exc_info=True)
        return set()


def component_diff(repo_path: Path, files: list[str]) -> str:
    """Return the unified diff for *files* against HEAD.

    Returns "" when files is empty, repo_path is not a git repo, or any error occurs.
    """
    if not files:
        return ""
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD", "--"] + list(files),
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.debug("git diff failed: %s", result.stderr.strip())
            return ""
        return result.stdout
    except Exception:
        logger.debug("component_diff failed", exc_info=True)
        return ""
