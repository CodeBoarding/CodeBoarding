from __future__ import annotations

from pathlib import Path

from cache._paths import get_repo_cache_dir, get_static_incremental_cache_path
from static_analyzer.analysis_cache import AnalysisCacheManager
from static_analyzer.graph import ClusterResult

type StaticAnalysisPayload = dict[str, object]


class StaticAnalysisCache:
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
        analysis_result: StaticAnalysisPayload,
        commit_hash: str,
        iteration_id: int,
    ) -> None:
        """Persist static-analysis output for a specific cache file and commit."""
        self.manager.save_cache(cache_path, analysis_result, commit_hash, iteration_id)

    def load_cache(self, cache_path: Path) -> tuple[StaticAnalysisPayload, str, int] | None:
        """Load static-analysis output and metadata from a cache file if valid."""
        return self.manager.load_cache(cache_path)

    def save_cache_with_clusters(
        self,
        cache_path: Path,
        analysis_result: StaticAnalysisPayload,
        cluster_results: dict[str, ClusterResult],
        commit_hash: str,
        iteration_id: int,
    ) -> None:
        """Persist static-analysis output plus precomputed cluster results."""
        self.manager.save_cache_with_clusters(cache_path, analysis_result, cluster_results, commit_hash, iteration_id)

    def load_cache_with_clusters(
        self, cache_path: Path
    ) -> tuple[StaticAnalysisPayload, dict[str, ClusterResult], str, int] | None:
        """Load static-analysis output with cluster results and cache metadata."""
        return self.manager.load_cache_with_clusters(cache_path)
