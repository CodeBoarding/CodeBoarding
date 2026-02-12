from __future__ import annotations

from pathlib import Path
from typing import Any

from cache._paths import get_repo_cache_dir, get_static_incremental_cache_path
from static_analyzer.analysis_cache import AnalysisCacheManager
from static_analyzer.graph import ClusterResult


class StaticCache:
    """Public static-analysis cache facade built on top of AnalysisCacheManager."""

    def __init__(
        self,
        repo_path: Path,
        cache_dir: Path | None = None,
        manager: AnalysisCacheManager | None = None,
    ):
        self.repo_path = Path(repo_path)
        self.cache_dir = Path(cache_dir) if cache_dir is not None else get_repo_cache_dir(self.repo_path)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.manager = manager or AnalysisCacheManager()

    def get_client_cache_path(self, language: str, project_path: Path) -> Path:
        """
        Resolve stable incremental cache path for one language client.

        Identity keys:
        - language: isolates analyzers for each programming language.
        - project_path hash: isolates monorepo subprojects sharing one language.
        """
        return get_static_incremental_cache_path(self.cache_dir, language, project_path)

    def save_cache(
        self,
        cache_path: Path,
        analysis_result: dict[str, Any],
        commit_hash: str,
        iteration_id: int,
    ) -> None:
        self.manager.save_cache(cache_path, analysis_result, commit_hash, iteration_id)

    def load_cache(self, cache_path: Path) -> tuple[dict[str, Any], str, int] | None:
        return self.manager.load_cache(cache_path)

    def save_cache_with_clusters(
        self,
        cache_path: Path,
        analysis_result: dict[str, Any],
        cluster_results: dict[str, ClusterResult],
        commit_hash: str,
        iteration_id: int,
    ) -> None:
        self.manager.save_cache_with_clusters(cache_path, analysis_result, cluster_results, commit_hash, iteration_id)

    def load_cache_with_clusters(self, cache_path: Path) -> tuple[dict, dict[str, ClusterResult], str, int] | None:
        return self.manager.load_cache_with_clusters(cache_path)
