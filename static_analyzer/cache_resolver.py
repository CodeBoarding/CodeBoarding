from __future__ import annotations

from pathlib import Path

from cache.static_cache import StaticCache


def resolve_static_cache(repo_path: Path, cache_dir: Path | None) -> StaticCache:
    """Resolve the static cache facade for the current repository."""
    return StaticCache(repo_path=repo_path, cache_dir=cache_dir)


def resolve_static_cache_dir(repo_path: Path, cache_dir: Path | None) -> Path:
    """Resolve cache directory for static analysis."""
    return resolve_static_cache(repo_path, cache_dir).cache_dir


def resolve_incremental_cache_path(cache: StaticCache | Path, language: str, project_path: Path) -> Path:
    """Resolve per-client cache path for incremental static analysis."""
    if isinstance(cache, StaticCache):
        return cache.get_client_cache_path(language, project_path)
    return StaticCache(repo_path=project_path, cache_dir=Path(cache)).get_client_cache_path(language, project_path)
