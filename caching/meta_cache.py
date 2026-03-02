import hashlib
import logging
from collections.abc import Sequence
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from agents.agent_responses import MetaAnalysisInsights
from agents.dependency_discovery import FileRole, discover_dependency_files
from caching.cache import CACHE_VERSION, BaseCache, ModelSettings
from repo_utils.ignore import RepoIgnoreManager
from utils import fingerprint_file

logger = logging.getLogger(__name__)


_README_PATTERNS: tuple[str, ...] = (
    "README.md",
    "README.rst",
    "README.txt",
    "README",
    "readme.md",
)

_CACHE_WATCH_ROLES: frozenset[FileRole] = frozenset({FileRole.MANIFEST, FileRole.CONFIG})


class MetaCacheKey(BaseModel):
    cache_version: int = CACHE_VERSION
    prompt: str
    agent_model: str
    agent_model_settings: ModelSettings
    parsing_model: str
    parsing_model_settings: ModelSettings
    metadata_files: list[str]
    metadata_content_hash: str


class MetaCache(BaseCache[MetaCacheKey, MetaAnalysisInsights]):
    """SQLite-backed cache for MetaAgent analysis results."""

    _META_RUN_ID = "meta"

    def __init__(
        self,
        repo_dir: Path,
        ignore_manager: RepoIgnoreManager,
    ):
        super().__init__("meta_agent_llm.sqlite", value_type=MetaAnalysisInsights, repo_dir=repo_dir)
        self._repo_dir = repo_dir
        self._ignore_manager = ignore_manager

    def discover_metadata_files(self) -> list[Path]:
        """Return dependency and README files whose changes invalidate this cache."""
        files = {
            discovered.path.relative_to(self._repo_dir)
            for discovered in discover_dependency_files(self._repo_dir, self._ignore_manager, roles=_CACHE_WATCH_ROLES)
        }

        for pattern in _README_PATTERNS:
            path = self._repo_dir / pattern
            if path.is_file() and not self._ignore_manager.should_ignore(path):
                files.add(path.relative_to(self._repo_dir))

        return sorted(files)

    def build_key(self, prompt: str, agent_llm: BaseChatModel, parsing_llm: BaseChatModel) -> MetaCacheKey:
        metadata_files = self.discover_metadata_files()
        metadata_content_hash = self._compute_metadata_content_hash(metadata_files)
        agent_model = (
            getattr(agent_llm, "model_name", None) or getattr(agent_llm, "model", None) or agent_llm.__class__.__name__
        )
        parsing_model = (
            getattr(parsing_llm, "model_name", None)
            or getattr(parsing_llm, "model", None)
            or parsing_llm.__class__.__name__
        )
        agent_model_settings = ModelSettings.from_chat_model(provider="unknown", llm=agent_llm)
        parsing_model_settings = ModelSettings.from_chat_model(provider="unknown", llm=parsing_llm)
        return MetaCacheKey(
            prompt=prompt,
            agent_model=str(agent_model),
            agent_model_settings=agent_model_settings,
            parsing_model=str(parsing_model),
            parsing_model_settings=parsing_model_settings,
            metadata_files=[path.as_posix() for path in metadata_files],
            metadata_content_hash=metadata_content_hash,
        )

    def _compute_metadata_content_hash(self, metadata_files: Sequence[Path]) -> str:
        """Return a deterministic fingerprint for watched file contents."""
        if not metadata_files:
            logger.debug("No metadata files discovered for meta cache key")
            return hashlib.sha256(b"").hexdigest()

        digest = hashlib.sha256()
        normalized_paths = sorted(metadata_files)

        for path in normalized_paths:
            normalized = path.as_posix()
            if (file_digest := fingerprint_file(self._repo_dir / path)) is None:
                logger.warning("Unable to fingerprint meta cache watch file: %s", normalized)
                return hashlib.sha256(b"").hexdigest()

            digest.update(normalized.encode("utf-8") + b"\0" + file_digest + b"\n")
        return digest.hexdigest()

    def store_meta_analysis(self, key: MetaCacheKey, value: MetaAnalysisInsights) -> None:
        self.store(key, value, run_id=self._META_RUN_ID)
