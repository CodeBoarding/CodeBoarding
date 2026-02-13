from __future__ import annotations

from pathlib import Path

from cache._paths import get_repo_cache_dir, get_static_incremental_cache_path


class StaticAnalysisCache:
    """Public static-analysis cache facade: path management for incremental analysis."""

    def __init__(self, repo_path: Path, cache_dir: Path | None = None):
        self.repo_path = Path(repo_path)
        self.cache_dir = Path(cache_dir) if cache_dir is not None else get_repo_cache_dir(self.repo_path)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_client_cache_path(self, language: str, project_path: Path) -> Path:
        """Resolve stable incremental cache path for one language client."""
        return get_static_incremental_cache_path(self.cache_dir, language, project_path)
