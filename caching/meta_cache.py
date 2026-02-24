import hashlib
import logging
from collections.abc import Sequence
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from agents.agent_responses import MetaAnalysisInsights
from agents.dependency_discovery import FileRole, discover_dependency_files
from caching.cache import BaseCache, ModelSettings
from repo_utils.ignore import RepoIgnoreManager
from utils import fingerprint_file, get_cache_dir

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
    meta: MetaAnalysisInsights
    base_commit: str
    watch_files: list[str]
    watch_state_hash: str | None = None


class MetaCache(BaseCache[str, MetaCacheKey]):
    """SQLite-backed cache for MetaAgent analysis results."""

    def __init__(
        self,
        repo_dir: Path,
        ignore_manager: RepoIgnoreManager,
        project_name: str = "",
        agent_llm: BaseChatModel | None = None,
        parsing_llm: BaseChatModel | None = None,
        prompt_material: str = "",
    ):
        super().__init__("meta_agent_llm.sqlite", cache_dir=get_cache_dir(repo_dir))
        self._repo_dir = repo_dir
        self._ignore_manager = ignore_manager
        self._prompt_key: str = self.signature({"project_name": project_name, "prompt_material": prompt_material})
        self._llm_key: str = self.signature(
            {
                "agent_model_settings": ModelSettings.from_chat_model("meta_agent", agent_llm).model_dump(mode="json"),
                "parsing_model_settings": ModelSettings.from_chat_model("meta_parser", parsing_llm).model_dump(
                    mode="json"
                ),
            }
        )

    def discover_watch_files(self) -> list[str]:
        """Return dependency and README files whose changes invalidate this cache."""
        files = {
            discovered.path.relative_to(self._repo_dir).as_posix()
            for discovered in discover_dependency_files(self._repo_dir, self._ignore_manager, roles=_CACHE_WATCH_ROLES)
        }

        for pattern in _README_PATTERNS:
            path = self._repo_dir / pattern
            if path.is_file() and not self._ignore_manager.should_ignore(path):
                files.add(path.relative_to(self._repo_dir).as_posix())

        return sorted(files)

    def _compute_metadata_content_hash(self, metadata_files: Sequence[str | Path]) -> str | None:
        """Return a deterministic fingerprint for watched file contents."""
        if not metadata_files:
            logger.error("[MetaCache] Trying to compute hash for empty list of watch files.")
            return None

        digest = hashlib.sha256()
        normalized_paths = sorted({Path(path) for path in metadata_files})

        for path in normalized_paths:
            normalized = path.as_posix()
            if (file_digest := fingerprint_file(self._repo_dir / path)) is None:
                logger.warning("Unable to fingerprint meta cache watch file: %s", normalized)
                return None

            digest.update(normalized.encode("utf-8") + b"\0" + file_digest + b"\n")
        return digest.hexdigest()

    def cache_keys(self, key: str) -> tuple[str, str]:
        prompt_key = self.signature({"scope": key, "prompt_key": self._prompt_key})
        return prompt_key, self._llm_key

    def load_record(self) -> MetaCacheKey | None:
        return super().load("meta_context")

    def store_record(self, record: MetaCacheKey) -> None:
        super().store("meta_context", record)

    def is_record_stale(self, record: MetaCacheKey) -> bool:
        """Return True if metadata file fingerprints differ from the cached record."""
        if not record.watch_files:
            return False
        if not record.watch_state_hash:
            logger.info("Meta cache record is missing watch-state fingerprint; recomputing once for migration")
            return True
        current_hash = self._compute_metadata_content_hash(self.discover_watch_files())
        return current_hash != record.watch_state_hash
