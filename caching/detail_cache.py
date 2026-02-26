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

    def __init__(self, repo_dir: Path):
        super().__init__("final_analysis_llm.sqlite", value_type=AnalysisInsights)
        self._repo_dir = repo_dir


class ClusterCache(BaseCache[DetailsCacheKey, ClusterAnalysis]):
    """SQLite-backed cache for cluster analysis results."""

    def __init__(self, repo_dir: Path):
        super().__init__("cluster_analysis_llm.sqlite", value_type=ClusterAnalysis)
        self._repo_dir = repo_dir
