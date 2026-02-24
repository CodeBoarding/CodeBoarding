import logging
from pathlib import Path

from pydantic import BaseModel

from agents.agent_responses import AnalysisInsights
from caching.cache import BaseCache, ModelSettings
from utils import get_cache_dir

logger = logging.getLogger(__name__)


class DetailsCacheKey(BaseModel):
    prompt: str
    model: str
    model_settings: ModelSettings


class DetailsCache(BaseCache[DetailsCacheKey, AnalysisInsights]):
    """SQLite-backed cache for detail agent analysis results."""

    def __init__(self, repo_dir: Path):
        super().__init__("details_agent_llm.sqlite", cache_dir=get_cache_dir(repo_dir))
        self._repo_dir = repo_dir

    def cache_keys(self, key: DetailsCacheKey) -> tuple[str, str]:
        prompt_key = self.signature(key.prompt)
        llm_key = self.signature({"model": key.model, "model_settings": key.model_settings.model_dump(mode="json")})
        return prompt_key, llm_key

    def load_entry(self, key: DetailsCacheKey) -> AnalysisInsights | None:
        return super().load(key)

    def store_entry(self, key: DetailsCacheKey, value: AnalysisInsights) -> None:
        super().store(key, value)

    def is_entry_stale(self, key: DetailsCacheKey) -> bool:
        return super().is_stale(key)
