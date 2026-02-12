from __future__ import annotations

import hashlib
from pathlib import Path

CACHE_ROOT_DIRNAME = ".codeboarding"
CACHE_DIRNAME = "cache"
META_CACHE_DB_FILENAME = "meta_cache.sqlite"


def get_repo_cache_dir(repo_dir: Path) -> Path:
    """Return the standard repository-local cache directory."""
    return repo_dir / CACHE_ROOT_DIRNAME / CACHE_DIRNAME


def ensure_repo_cache_dir(repo_dir: Path) -> Path:
    """Create and return the standard repository-local cache directory."""
    cache_dir = get_repo_cache_dir(repo_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_meta_cache_db_path(repo_dir: Path) -> Path:
    """Return the SQLite file path used by metadata cache."""
    return ensure_repo_cache_dir(repo_dir) / META_CACHE_DB_FILENAME


def build_static_client_cache_id(language: str, project_path: Path) -> str:
    """
    Build a stable cache identity per static-analysis client.

    Identity keys:
    - language: isolates analyzers for each programming language.
    - project_path hash: isolates monorepo subprojects sharing one language.
    """
    normalized_path = str(project_path.resolve())
    path_hash = hashlib.sha1(normalized_path.encode("utf-8")).hexdigest()[:12]
    return f"{language.lower()}_{path_hash}"


def get_static_incremental_cache_path(cache_dir: Path, language: str, project_path: Path) -> Path:
    """Return per-client incremental static cache path under the given cache directory."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    client_id = build_static_client_cache_id(language, project_path)
    return cache_dir / f"incremental_cache_{client_id}.json"
