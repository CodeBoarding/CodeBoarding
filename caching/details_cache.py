import logging
from pathlib import Path

from langchain_core.outputs import Generation
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

    def __init__(self, repo_dir: Path):
        super().__init__("final_analysis_llm.sqlite", value_type=AnalysisInsights)
        self._repo_dir = repo_dir

    def load(self, key: DetailsCacheKey | str) -> AnalysisInsights | None:
        cache = self._open_sqlite()
        if cache is None:
            return None

        try:
            key_signature = self.signature(key)
            raw = cache.lookup(key_signature, self._LLM_NAMESPACE)
            if not raw:
                logger.info("Cache miss: %s key=%s", self.file_path.name, key_signature)
                return None
            value = AnalysisInsights.model_validate_json(raw[0].text)
            logger.info("Cache load success: %s key=%s", self.file_path.name, key_signature)
            return value
        except Exception as e:
            logger.warning("Cache load failed: %s", e)
            return None

    def store(self, key: DetailsCacheKey | str, value: AnalysisInsights) -> None:
        cache = self._open_sqlite()
        if cache is None:
            return

        try:
            key_sig = self.signature(key)
            cache.update(key_sig, self._LLM_NAMESPACE, [Generation(text=value.model_dump_json())])
            logger.info("Cache store success: %s key=%s", self.file_path.name, key_sig)
        except Exception as e:
            logger.warning("Cache store failed: %s", e)


class ClusterCache(BaseCache[DetailsCacheKey, ClusterAnalysis]):
    """SQLite-backed cache for cluster analysis results."""

    _LLM_NAMESPACE = "details_cluster_analysis"

    def __init__(self, repo_dir: Path):
        super().__init__("cluster_analysis_llm.sqlite", value_type=ClusterAnalysis)
        self._repo_dir = repo_dir

    def load(self, key: DetailsCacheKey | str) -> ClusterAnalysis | None:
        cache = self._open_sqlite()
        if cache is None:
            return None

        try:
            key_signature = self.signature(key)
            raw = cache.lookup(key_signature, self._LLM_NAMESPACE)
            if not raw:
                logger.info("Cache miss: %s key=%s", self.file_path.name, key_signature)
                return None
            value = ClusterAnalysis.model_validate_json(raw[0].text)
            logger.info("Cache load success: %s key=%s", self.file_path.name, key_signature)
            return value
        except Exception as e:
            logger.warning("Cache load failed: %s", e)
            return None

    def store(self, key: DetailsCacheKey | str, value: ClusterAnalysis) -> None:
        cache = self._open_sqlite()
        if cache is None:
            return

        try:
            key_sig = self.signature(key)
            cache.update(key_sig, self._LLM_NAMESPACE, [Generation(text=value.model_dump_json())])
            logger.info("Cache store success: %s key=%s", self.file_path.name, key_sig)
        except Exception as e:
            logger.warning("Cache store failed: %s", e)
