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
