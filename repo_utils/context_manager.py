"""Context management for switching between focused file sets and analysis states."""

import logging
import shutil
from pathlib import Path

from repo_utils.include_config import INCLUDE_FILENAME, validate_include_patterns

logger = logging.getLogger(__name__)

CONTEXTS_DIR = "contexts"
GLOBAL_CONTEXT = "global"
ANALYSIS_JSON = "analysis.json"
FILE_COVERAGE_JSON = "file_coverage.json"


def _get_codeboarding_dir(repo_root: Path) -> Path:
    return repo_root / ".codeboarding"


def _get_contexts_dir(repo_root: Path) -> Path:
    return _get_codeboarding_dir(repo_root) / CONTEXTS_DIR


def _get_context_dir(repo_root: Path, name: str) -> Path:
    return _get_contexts_dir(repo_root) / name


def list_contexts(repo_root: Path) -> list[str]:
    """Return sorted list of saved context names."""
    contexts_dir = _get_contexts_dir(repo_root)
    if not contexts_dir.exists():
        return []
    return sorted([d.name for d in contexts_dir.iterdir() if d.is_dir()])


def create_context(repo_root: Path, name: str) -> Path:
    """Create a new context directory with an empty .codeboarding-include file.

    Returns the path to the created context directory.
    """
    if name == GLOBAL_CONTEXT:
        raise ValueError(f"Cannot create context named '{GLOBAL_CONTEXT}' — reserved name")

    context_dir = _get_context_dir(repo_root, name)
    if context_dir.exists():
        raise ValueError(f"Context '{name}' already exists")

    context_dir.mkdir(parents=True, exist_ok=True)
    include_path = context_dir / INCLUDE_FILENAME
    include_path.touch()
    logger.info(f"Created context '{name}' at {context_dir}")
    return context_dir


def set_context(repo_root: Path, name: str) -> None:
    """Switch to a context by copying its files to the .codeboarding root.

    For 'global': removes .codeboarding-include from root, copies global analysis files.
    For named contexts: copies .codeboarding-include, analysis.json, file_coverage.json to root.
    """
    codeboarding_dir = _get_codeboarding_dir(repo_root)
    context_dir = _get_context_dir(repo_root, name)

    # Auto-create global context if it doesn't exist
    if name == GLOBAL_CONTEXT and not context_dir.exists():
        context_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Auto-created global context directory")

    if not context_dir.exists():
        raise ValueError(f"Context '{name}' does not exist")

    # Ensure global context exists if setting to global
    if name == GLOBAL_CONTEXT:
        # Remove .codeboarding-include from root
        include_path = codeboarding_dir / INCLUDE_FILENAME
        if include_path.exists():
            include_path.unlink()
            logger.info(f"Removed {INCLUDE_FILENAME} for global context")

        # Copy global analysis files to root if they exist
        _copy_if_exists(context_dir / ANALYSIS_JSON, codeboarding_dir / ANALYSIS_JSON)
        _copy_if_exists(context_dir / FILE_COVERAGE_JSON, codeboarding_dir / FILE_COVERAGE_JSON)
    else:
        # Validate include patterns before switching
        # Temporarily copy include file to root for validation
        src_include = context_dir / INCLUDE_FILENAME
        dst_include = codeboarding_dir / INCLUDE_FILENAME

        if src_include.exists():
            shutil.copy2(src_include, dst_include)
            errors = validate_include_patterns(repo_root)
            if errors:
                dst_include.unlink(missing_ok=True)
                raise ValueError(f"Invalid {INCLUDE_FILENAME} in context '{name}': {'; '.join(errors)}")
        else:
            logger.warning(f"Context '{name}' has no {INCLUDE_FILENAME}")

        # Copy analysis files
        _copy_if_exists(context_dir / ANALYSIS_JSON, codeboarding_dir / ANALYSIS_JSON)
        _copy_if_exists(context_dir / FILE_COVERAGE_JSON, codeboarding_dir / FILE_COVERAGE_JSON)

    logger.info(f"Switched to context '{name}'")


def save_context(repo_root: Path, name: str) -> None:
    """Save current analysis files from .codeboarding root to a context directory.

    Creates the context directory if it doesn't exist (auto-creates global on first run).
    """
    codeboarding_dir = _get_codeboarding_dir(repo_root)
    context_dir = _get_context_dir(repo_root, name)

    # Auto-create context dir if it doesn't exist
    if not context_dir.exists():
        context_dir.mkdir(parents=True, exist_ok=True)
        if name == GLOBAL_CONTEXT:
            logger.info(f"Auto-created global context directory")

    # Copy analysis files from root to context
    _copy_if_exists(codeboarding_dir / ANALYSIS_JSON, context_dir / ANALYSIS_JSON)
    _copy_if_exists(codeboarding_dir / FILE_COVERAGE_JSON, context_dir / FILE_COVERAGE_JSON)

    # For non-global contexts, also copy .codeboarding-include if it exists
    if name != GLOBAL_CONTEXT:
        src_include = codeboarding_dir / INCLUDE_FILENAME
        if src_include.exists():
            shutil.copy2(src_include, context_dir / INCLUDE_FILENAME)

    logger.info(f"Saved analysis to context '{name}'")


def delete_context(repo_root: Path, name: str) -> None:
    """Delete a context directory."""
    if name == GLOBAL_CONTEXT:
        raise ValueError(f"Cannot delete '{GLOBAL_CONTEXT}' context — reserved name")

    context_dir = _get_context_dir(repo_root, name)
    if not context_dir.exists():
        raise ValueError(f"Context '{name}' does not exist")

    shutil.rmtree(context_dir)
    logger.info(f"Deleted context '{name}'")


def get_current_context(repo_root: Path) -> str:
    """Detect the currently active context name.

    Returns 'global' if no .codeboarding-include exists in root.
    Otherwise, matches the include file against context directories.
    """
    codeboarding_dir = _get_codeboarding_dir(repo_root)
    include_path = codeboarding_dir / INCLUDE_FILENAME

    if not include_path.exists():
        return GLOBAL_CONTEXT

    # Try to match include file against context directories
    contexts_dir = _get_contexts_dir(repo_root)
    if contexts_dir.exists():
        for context_dir in contexts_dir.iterdir():
            if context_dir.is_dir():
                ctx_include = context_dir / INCLUDE_FILENAME
                if ctx_include.exists():
                    # Compare file contents
                    try:
                        if include_path.read_bytes() == ctx_include.read_bytes():
                            return context_dir.name
                    except Exception:
                        continue

    return GLOBAL_CONTEXT


def ensure_global_context(repo_root: Path) -> None:
    """Ensure the global context directory exists."""
    global_dir = _get_context_dir(repo_root, GLOBAL_CONTEXT)
    if not global_dir.exists():
        global_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created global context directory")


def _copy_if_exists(src: Path, dst: Path) -> None:
    """Copy file if source exists, silently skip otherwise."""
    if src.exists():
        shutil.copy2(src, dst)
        logger.debug(f"Copied {src.name} to {dst.parent}")
