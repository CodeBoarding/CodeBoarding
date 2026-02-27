import logging
from pathlib import Path

from langchain_core.outputs import Generation
from pydantic import BaseModel
from sqlalchemy import Column, MetaData, String, Table, create_engine, func, or_

from agents.agent_responses import AnalysisInsights, ClusterAnalysis
from caching.cache import CACHE_VERSION, BaseCache, ModelSettings

logger = logging.getLogger(__name__)


class AnalysisInsightsValue(AnalysisInsights):
    run_id: str


class ClusterAnalysisValue(ClusterAnalysis):
    run_id: str


class DetailsCacheKey(BaseModel):
    cache_version: int = CACHE_VERSION
    prompt: str
    model_settings: ModelSettings


class FinalAnalysisCache(BaseCache[DetailsCacheKey, AnalysisInsightsValue]):
    """SQLite-backed cache for detail agent analysis results."""

    _LLM_NAMESPACE = "details_final_analysis"

    def __init__(self, repo_dir: Path):
        super().__init__("final_analysis_llm.sqlite", value_type=AnalysisInsightsValue, repo_dir=repo_dir)
        self._repo_dir = repo_dir

    @staticmethod
    def build_key(prompt: str, model_settings: ModelSettings) -> DetailsCacheKey:
        return DetailsCacheKey(prompt=prompt, model_settings=model_settings)

    def load(self, key: DetailsCacheKey) -> AnalysisInsightsValue | None:
        cache = self._open_sqlite()
        if cache is None:
            return None

        try:
            key_signature = self.signature(key)
            raw = cache.lookup(key_signature, self._LLM_NAMESPACE)
            if not raw:
                logger.info("Cache miss: %s key=%s", self.file_path.name, key_signature)
                return None
            value = AnalysisInsightsValue.model_validate_json(raw[0].text)
            logger.info("Cache load success: %s key=%s", self.file_path.name, key_signature)
            return value
        except Exception as e:
            logger.warning("Cache load failed: %s", e)
            return None

    def store(self, key: DetailsCacheKey, value: AnalysisInsightsValue) -> None:
        cache = self._open_sqlite()
        if cache is None:
            return

        try:
            key_sig = self.signature(key)
            cache.update(key_sig, self._LLM_NAMESPACE, [Generation(text=value.model_dump_json())])
            logger.info("Cache store success: %s key=%s", self.file_path.name, key_sig)
        except Exception as e:
            logger.warning("Cache store failed: %s", e)

    def clear(self, only_keep_run_id: str) -> None:
        _clear_namespace_entries_except_run_id(self.file_path, self._LLM_NAMESPACE, only_keep_run_id)


class ClusterCache(BaseCache[DetailsCacheKey, ClusterAnalysisValue]):
    """SQLite-backed cache for cluster analysis results."""

    _LLM_NAMESPACE = "details_cluster_analysis"

    def __init__(self, repo_dir: Path):
        super().__init__("cluster_analysis_llm.sqlite", value_type=ClusterAnalysisValue, repo_dir=repo_dir)
        self._repo_dir = repo_dir

    @staticmethod
    def build_key(prompt: str, model_settings: ModelSettings) -> DetailsCacheKey:
        return DetailsCacheKey(prompt=prompt, model_settings=model_settings)

    def load(self, key: DetailsCacheKey) -> ClusterAnalysisValue | None:
        cache = self._open_sqlite()
        if cache is None:
            return None

        try:
            key_signature = self.signature(key)
            raw = cache.lookup(key_signature, self._LLM_NAMESPACE)
            if not raw:
                logger.info("Cache miss: %s key=%s", self.file_path.name, key_signature)
                return None
            value = ClusterAnalysisValue.model_validate_json(raw[0].text)
            logger.info("Cache load success: %s key=%s", self.file_path.name, key_signature)
            return value
        except Exception as e:
            logger.warning("Cache load failed: %s", e)
            return None

    def store(self, key: DetailsCacheKey, value: ClusterAnalysisValue) -> None:
        cache = self._open_sqlite()
        if cache is None:
            return

        try:
            key_sig = self.signature(key)
            cache.update(key_sig, self._LLM_NAMESPACE, [Generation(text=value.model_dump_json())])
            logger.info("Cache store success: %s key=%s", self.file_path.name, key_sig)
        except Exception as e:
            logger.warning("Cache store failed: %s", e)

    def clear(self, only_keep_run_id: str) -> None:
        _clear_namespace_entries_except_run_id(self.file_path, self._LLM_NAMESPACE, only_keep_run_id)


def _clear_namespace_entries_except_run_id(file_path: Path, namespace: str, only_keep_run_id: str) -> None:
    """Delete all records in a namespace except those with the specified run_id."""
    engine = create_engine(f"sqlite:///{file_path}")
    metadata = MetaData()

    full_llm_cache = Table(
        "full_llm_cache",  # This is LangChain's default table name. Needed!
        metadata,
        Column("llm", String),
        Column("response", String),
    )

    try:
        with engine.begin() as conn:
            run_id_expr = func.json_extract(func.json_extract(full_llm_cache.c.response, "$.kwargs.text"), "$.run_id")
            stmt = full_llm_cache.delete().where(
                full_llm_cache.c.llm == namespace,
                or_(run_id_expr.is_(None), run_id_expr != only_keep_run_id),
            )

            result = conn.execute(stmt)
            deleted = result.rowcount

            logger.info(
                "Cleared %s rows from %s while keeping run_id=%s",
                deleted,
                namespace,
                only_keep_run_id,
            )
    except Exception as e:
        logger.warning(
            "Cache clear failed for namespace=%s keep_run_id=%s: %s",
            namespace,
            only_keep_run_id,
            e,
        )


def _load_existing_run_id(file_path: Path, namespace: str) -> str | None:
    engine = create_engine(f"sqlite:///{file_path}")
    metadata = MetaData()

    full_llm_cache = Table(
        "full_llm_cache",
        metadata,
        Column("llm", String),
        Column("response", String),
    )

    try:
        with engine.connect() as conn:
            run_id_expr = func.json_extract(func.json_extract(full_llm_cache.c.response, "$.kwargs.text"), "$.run_id")
            stmt = (
                full_llm_cache.select().with_only_columns(run_id_expr).where(full_llm_cache.c.llm == namespace).limit(1)
            )
            run_id = conn.execute(stmt).scalar_one_or_none()
            return str(run_id) if run_id is not None else None
    except Exception as e:
        logger.warning("Cache run_id load failed for namespace=%s: %s", namespace, e)
        return None


def prune_details_caches(repo_dir: Path, only_keep_run_id: str) -> None:
    FinalAnalysisCache(repo_dir).clear(only_keep_run_id=only_keep_run_id)
    ClusterCache(repo_dir).clear(only_keep_run_id=only_keep_run_id)
