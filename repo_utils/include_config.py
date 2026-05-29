"""Parse and validate .codeboarding-include patterns."""

import logging
from pathlib import Path

import pathspec

logger = logging.getLogger(__name__)

INCLUDE_FILENAME = ".codeboarding-include"


def load_include_patterns(repo_root: Path) -> pathspec.PathSpec | None:
    """Load .codeboarding-include from repo root and compile a PathSpec.

    Returns None if the file doesn't exist or contains only comments/blank lines.
    """
    include_path = repo_root / INCLUDE_FILENAME
    if not include_path.exists():
        return None

    try:
        lines = include_path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        logger.warning(f"Failed to read {include_path}: {e}")
        return None

    # Filter out comments and blank lines
    active_lines = [line for line in lines if line.strip() and not line.strip().startswith("#")]
    if not active_lines:
        logger.warning(f"{include_path} exists but contains no active patterns")
        return None

    return pathspec.PathSpec.from_lines("gitwildmatch", active_lines)


def validate_include_patterns(repo_root: Path) -> list[str]:
    """Return list of validation errors for .codeboarding-include, empty if valid."""
    include_path = repo_root / INCLUDE_FILENAME
    if not include_path.exists():
        return []

    try:
        lines = include_path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        return [f"Cannot read {include_path}: {e}"]

    active_lines = [line for line in lines if line.strip() and not line.strip().startswith("#")]
    if not active_lines:
        return [f"{INCLUDE_FILENAME} exists but contains no active patterns — nothing will be included"]

    return []
