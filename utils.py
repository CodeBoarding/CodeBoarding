import logging
import os
import re
import shutil
import hashlib
import uuid
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

CODEBOARDING_DIR_NAME = ".codeboarding"
ANALYSIS_FILENAME = "analysis.json"


class CFGGenerationError(Exception):
    pass


def create_temp_repo_folder():
    unique_id = uuid.uuid4().hex
    temp_dir = os.path.join("temp", unique_id)
    os.makedirs(temp_dir, exist_ok=False)
    return Path(temp_dir)


def remove_temp_repo_folder(temp_path: str):
    p = Path(temp_path)
    if not p.parts or p.parts[0] != "temp":
        raise ValueError(f"Refusing to delete outside of 'temp/': {temp_path!r}")
    shutil.rmtree(temp_path)


def get_cache_dir(repo_dir: Path) -> Path:
    return repo_dir / CODEBOARDING_DIR_NAME / "cache"


def get_project_root() -> Path:
    project_root_env = os.getenv("PROJECT_ROOT")
    if project_root_env:
        return Path(project_root_env)

    return Path(__file__).resolve().parent


def fingerprint_file(path: Path) -> bytes | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.digest()
    except OSError:
        return None


def monitoring_enabled():
    logger.info("Monitoring enabled: %s", os.getenv("ENABLE_MONITORING", "false"))
    return os.getenv("ENABLE_MONITORING", "false").lower() in ("1", "true", "yes")


def get_config(item_key: str):
    from tool_registry import build_config

    config = build_config()
    if item_key not in config:
        raise KeyError(f"Item '{item_key}' not found in configuration.")
    return config[item_key]


def to_relative_path(file_path: str, repo_root: Path) -> str:
    """Convert an absolute path to a repo-relative path with forward slashes for portable storage."""
    try:
        rel = Path(file_path).relative_to(repo_root)
        return rel.as_posix()
    except ValueError:
        return file_path


def to_absolute_path(file_path: str, repo_root: Path) -> str:
    """Expand a (possibly Windows-style) repo-relative path back to an absolute path."""
    # Normalise separator before constructing a Path so that backslashes from
    # Windows-written caches are treated as path separators on POSIX systems.
    normalised = file_path.replace("\\", "/")
    p = Path(normalised)
    if p.is_absolute():
        return str(p)
    return str(repo_root / p)


def sanitize(name: str) -> str:
    """Replace non-alphanumerics with underscores so IDs are valid identifiers."""
    return re.sub(r"\W+", "_", name)


def generate_run_id() -> str:
    return uuid.uuid4().hex


def copy_files(files: Iterable[Path], target_dir: Path) -> None:
    """Copy each file in *files* into *target_dir*, preserving metadata."""
    target_dir.mkdir(parents=True, exist_ok=True)
    for file in files:
        dest = target_dir / file.name
        shutil.copy2(file, dest)
        logger.info("Copied %s to %s", file.name, dest)
