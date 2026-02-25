import hashlib
import logging
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from agents.agent_responses import MetaAnalysisInsights
from agents.dependency_discovery import FileRole, discover_dependency_files
from caching.cache import BaseCache, ModelSettings
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
CACHE_VERSION = 1


class MetaCacheKey(BaseModel):
    cache_version = CACHE_VERSION
    prompt: str
    model: str
    model_settings: ModelSettings
    metadata_files: list[str]
    metadata_content_hash: str


class MetaCache(BaseCache[MetaCacheKey, MetaAnalysisInsights]):
    """SQLite-backed cache for MetaAgent analysis results."""

    def __init__(
        self,
        repo_dir: Path,
        ignore_manager: RepoIgnoreManager,
    ):
        super().__init__("meta_agent_llm.sqlite", value_type=MetaAnalysisInsights)
        self._repo_dir = repo_dir
        self._ignore_manager = ignore_manager

    def discover_metadata_files(self) -> list[Path]:
        """Return dependency and README files whose changes invalidate this cache."""
        files = {
            discovered.path.relative_to(self._repo_dir).as_posix()
            for discovered in discover_dependency_files(self._repo_dir, self._ignore_manager, roles=_CACHE_WATCH_ROLES)
        }

        for pattern in _README_PATTERNS:
            path = self._repo_dir / pattern
            if path.is_file() and not self._ignore_manager.should_ignore(path):
                files.add(path.relative_to(self._repo_dir))

        return files

    def _compute_metadata_content_hash(self, metadata_files: Sequence[Path]) -> str | None:
        """Return a deterministic fingerprint for watched file contents."""
        if not metadata_files:
            logger.error("[MetaCache] Trying to compute hash for empty list of watch files.")
            return None

        digest = hashlib.sha256()
        normalized_paths = sorted(metadata_files)

        for path in normalized_paths:
            normalized = path.as_posix()
            if (file_digest := fingerprint_file(self._repo_dir / path)) is None:
                logger.warning("Unable to fingerprint meta cache watch file: %s", normalized)
                return None

            digest.update(normalized.encode("utf-8") + b"\0" + file_digest + b"\n")
        return digest.hexdigest()

    def is_record_stale(self, record: MetaCacheKey) -> bool:
        """Return True if metadata file fingerprints differ from the cached record."""
        if not record.watch_files:
            logging.warning("Found no metadata files for cached meta cache record; recomputing...")
            return True
        if not record.metadata_content_hash:
            logger.info("Meta cache record is missing watch-state fingerprint; recomputing...")
            return True
        current_hash = self._compute_metadata_content_hash(self.discover_metadata_files())
        return current_hash != record.metadata_content_hash
