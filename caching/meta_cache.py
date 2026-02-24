import hashlib
import json
import logging
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from utils import fingerprint_file
from langchain_community.cache import SQLiteCache
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import Generation
from pydantic import BaseModel

from agents.agent_responses import MetaAnalysisInsights
from agents.dependency_discovery import FileRole, discover_dependency_files
from caching.cache import BaseCache
from repo_utils.ignore import RepoIgnoreManager
from utils import get_cache_dir

logger = logging.getLogger(__name__)

type JsonScalar = str | int | float | bool | None

_README_PATTERNS: tuple[str, ...] = (
    "README.md",
    "README.rst",
    "README.txt",
    "README",
    "readme.md",
)

_CACHE_WATCH_ROLES: frozenset[FileRole] = frozenset({FileRole.MANIFEST, FileRole.CONFIG})


class MetaCacheRecord(BaseModel):
    meta: MetaAnalysisInsights
    base_commit: str
    watch_files: list[str]
    watch_state_hash: str | None = None


class MetaCache(BaseCache[str, MetaCacheRecord]):
    """SQLite-backed cache for MetaAgent analysis results."""

    def __init__(
        self,
        repo_dir: Path,
        ignore_manager: RepoIgnoreManager,
        project_name: str,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
        prompt_material: str,
    ):
        super().__init__("meta_agent_llm.sqlite", cache_dir=get_cache_dir(repo_dir))
        self._repo_dir = repo_dir
        self._ignore_manager = ignore_manager
        self._prompt_key = self._build_prompt_key(project_name, prompt_material)
        self._llm_key = self._build_llm_key(agent_llm, parsing_llm)

    def discover_watch_files(self) -> list[str]:
        """Return dependency and README files whose changes invalidate this cache."""
        watch = {
            discovered.path.relative_to(self._repo_dir).as_posix()
            for discovered in discover_dependency_files(self._repo_dir, self._ignore_manager, roles=_CACHE_WATCH_ROLES)
        }

        for pattern in _README_PATTERNS:
            path = self._repo_dir / pattern
            if path.is_file() and not self._ignore_manager.should_ignore(path):
                watch.add(pattern)

        return sorted(watch)

    def _compute_metadata_content_hash(self, metadata_files: Sequence[str]) -> str:
        """Return a deterministic fingerprint for watched file contents."""
        if not metadata_files:
            return logger.error("[MetaCache] Trying to compute hash for empty list of metadata files.")

        digest = hashlib.sha256()
        for relative_path in sorted(set(metadata_files)):
            file_digest = fingerprint_file(self._repo_dir / relative_path)
            if file_digest is None:
                logger.warning("Unable to fingerprint metadata files: %s", relative_path)
                return None
            digest.update(relative_path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(file_digest)
            digest.update(b"\n")

        return digest.hexdigest()

    def is_stale(self, record: MetaCacheRecord) -> bool:
        """Return True if metadata file fingerprints differ from the cached record."""
        if not record.metadata_files:
            return True

        if not record.metadata_content_hash:
            logger.info("Meta cache record is missing watch-state fingerprint; recomputing once for migration")
            return True

        current_signature = self._compute_metadata_content_hash(self.discover_watch_files())
        return current_signature != record.metadata_content_hash
