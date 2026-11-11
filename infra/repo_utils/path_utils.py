import os
from pathlib import Path


def normalize_repo_path(file_path: str | Path, repo_root: Path | str | None = None) -> str:
    """Return a portable repo-relative path string."""
    normalized = str(file_path).replace("\\", "/")
    candidate = Path(normalized)
    if candidate.is_absolute() and repo_root is not None:
        try:
            return candidate.resolve().relative_to(Path(repo_root).resolve()).as_posix()
        except ValueError:
            return normalized

    normalized = os.path.normpath(normalized).replace("\\", "/")
    if normalized == ".":
        return ""
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def to_relative_path(file_path: str | Path, repo_root: Path) -> str:
    """Convert a path under *repo_root* to a portable repo-relative path."""
    path = Path(str(file_path).replace("\\", "/"))
    if not path.is_absolute():
        return normalize_repo_path(path)

    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(file_path)


def to_absolute_path(file_path: str | Path, repo_root: Path) -> str:
    """Expand a repo-relative path to an absolute path."""
    normalized = normalize_repo_path(file_path)
    path = Path(normalized)
    if path.is_absolute():
        return str(path)
    return str(repo_root / path)
