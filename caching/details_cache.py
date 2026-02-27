import logging
from pathlib import Path

from pydantic import BaseModel

from agents.agent_responses import AnalysisInsights, ClusterAnalysis
from caching.cache import CACHE_VERSION, BaseCache, ModelSettings

logger = logging.getLogger(__name__)


class DetailsCacheKey(BaseModel):
    cache_version: int = CACHE_VERSION
    prompt: str
    model_settings: ModelSettings


class FinalAnalysisCache(BaseCache[DetailsCacheKey, AnalysisInsights]):
    """SQLite-backed cache for detail agent analysis results."""

    _LLM_NAMESPACE = "details_final_analysis"
    _CLEAR_BEFORE_STORE = False
    _REQUIRE_RUN_ID = True

    def __init__(self, repo_dir: Path):
        super().__init__(
            "final_analysis_llm.sqlite",
            value_type=AnalysisInsights,
            repo_dir=repo_dir,
            namespace=self._LLM_NAMESPACE,
        )

    @staticmethod
    def build_key(prompt: str, model_settings: ModelSettings) -> DetailsCacheKey:
        return DetailsCacheKey(prompt=prompt, model_settings=model_settings)


class ClusterCache(BaseCache[DetailsCacheKey, ClusterAnalysis]):
    """SQLite-backed cache for cluster analysis results."""

    _LLM_NAMESPACE = "details_cluster_analysis"
    _CLEAR_BEFORE_STORE = False
    _REQUIRE_RUN_ID = True

    def __init__(self, repo_dir: Path):
        super().__init__(
            "cluster_analysis_llm.sqlite",
            value_type=ClusterAnalysis,
            repo_dir=repo_dir,
            namespace=self._LLM_NAMESPACE,
        )

    @staticmethod
    def build_key(prompt: str, model_settings: ModelSettings) -> DetailsCacheKey:
        return DetailsCacheKey(prompt=prompt, model_settings=model_settings)


def prune_details_caches(repo_dir: Path, only_keep_run_id: str) -> None:
    FinalAnalysisCache(repo_dir).clear(keep_run_ids=[only_keep_run_id])
    ClusterCache(repo_dir).clear(keep_run_ids=[only_keep_run_id])


def _load_existing_run_id(repo_dir: Path) -> str | None:
    final_cache = FinalAnalysisCache(repo_dir)
    final_ids = final_cache.load_existing_ids(namespace=FinalAnalysisCache._LLM_NAMESPACE)
    logger.info("Found %d existing run_id(s) in %s", len(final_ids), FinalAnalysisCache._LLM_NAMESPACE)
    if final_ids:
        logger.info("Returning first existing run_id from %s: %s", FinalAnalysisCache._LLM_NAMESPACE, final_ids[0])
        return final_ids[0]

    cluster_cache = ClusterCache(repo_dir)
    cluster_ids = cluster_cache.load_existing_ids(namespace=ClusterCache._LLM_NAMESPACE)
    if cluster_ids:
        return cluster_ids[0]
    return None
