import hashlib
import json
import logging
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from langchain_community.cache import SQLiteCache
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import Generation
from pydantic import BaseModel

from agents.agent_responses import MetaAnalysisInsights
from agents.dependency_discovery import FileRole, discover_dependency_files
from caching.cache import BaseCache
from repo_utils import Repo, require_git_import
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


class MetaCache(BaseCache[MetaCacheRecord]):
    """SQLite-backed cache for MetaAgent analysis results.

    Watches dependency manifests, config files, and root-level READMEs.
    Keyed by a composite of project name, prompt version hash, and LLM
    configuration so that any change to prompts or models automatically
    produces a cache miss.
    """

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

    @staticmethod
    def _llm_signature(llm: BaseChatModel) -> str:
        model_id = None
        for attr in ("model_name", "model", "model_id"):
            value = getattr(llm, attr, None)
            if isinstance(value, str) and value:
                model_id = value
                break

        config: dict[str, JsonScalar] = {}
        for attr in ("temperature", "max_tokens", "top_p", "timeout", "max_retries"):
            value = getattr(llm, attr, None)
            if isinstance(value, (str, int, float, bool)) or value is None:
                config[attr] = value

        payload = {
            "provider": f"{type(llm).__module__}.{type(llm).__name__}",
            "model_id": model_id or type(llm).__name__,
            "config": config,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def _build_prompt_key(self, project_name: str, prompt_material: str) -> str:
        prompt_hash = hashlib.sha256(prompt_material.encode("utf-8")).hexdigest()
        payload = {
            "kind": "meta_agent_cache",
            "project_name": project_name,
            "prompt_version": prompt_hash,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def _build_llm_key(self, agent_llm: BaseChatModel, parsing_llm: BaseChatModel) -> str:
        payload = {
            "kind": "meta_agent_llm_cache",
            "agent": self._llm_signature(agent_llm),
            "parser": self._llm_signature(parsing_llm),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def signature(self) -> str:
        """Return the composite cache key identifying this configuration."""
        return self._prompt_key + "|" + self._llm_key

    def _open_sqlite(self) -> SQLiteCache | None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            return SQLiteCache(database_path=str(self.file_path))
        except (OSError, sqlite3.Error) as e:
            logger.warning("Meta cache disabled: %s", e)
            return None

    def load(self) -> MetaCacheRecord | None:
        cache = self._open_sqlite()
        if cache is None:
            return None
        raw: Sequence[Generation] | None = cache.lookup(self._prompt_key, self._llm_key)
        if raw is None:
            return None
        if len(raw) > 1:
            logger.warning("Meta cache returned %d generations; using first", len(raw))
        try:
            return MetaCacheRecord.model_validate_json(raw[0].text)
        except Exception:
            return None

    def store(self, data: MetaCacheRecord) -> None:
        cache = self._open_sqlite()
        if cache is None:
            return
        cache.clear()
        cache.update(self._prompt_key, self._llm_key, [Generation(text=data.model_dump_json())])

    def clear(self) -> None:
        cache = self._open_sqlite()
        if cache is not None:
            cache.clear()

    @require_git_import(default=[])
    def discover_watch_files(self) -> list[str]:
        """Return git-known files whose changes should invalidate this cache.

        Includes dependency manifests and configs (not locks) and root-level
        README files that the meta agent reads for project context.
        """
        try:
            repo = Repo(self._repo_dir)
            tracked_files = set(repo.git.ls_files().splitlines())
            untracked_files = {
                Path(path).as_posix()
                for path in repo.untracked_files
                if not self._ignore_manager.should_ignore(Path(path))
            }
            git_known_files = tracked_files | untracked_files
        except Exception as e:
            logger.warning("Unable to discover git file set for meta cache watch list: %s", e)
            return []

        watch: set[str] = set()

        for discovered in discover_dependency_files(self._repo_dir, self._ignore_manager, roles=_CACHE_WATCH_ROLES):
            relative_path = discovered.path.relative_to(self._repo_dir).as_posix()
            if relative_path in git_known_files:
                watch.add(relative_path)

        for pattern in _README_PATTERNS:
            if (self._repo_dir / pattern).is_file() and pattern in git_known_files:
                watch.add(pattern)

        return sorted(watch)

    @staticmethod
    def _fingerprint_file(path: Path) -> bytes | None:
        try:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.digest()
        except OSError:
            return None

    def compute_watch_state_hash(self, watch_files: Sequence[str]) -> str | None:
        """Return a deterministic fingerprint for watched file contents."""
        if not watch_files:
            return None

        digest = hashlib.sha256()
        for relative_path in sorted(set(watch_files)):
            file_digest = self._fingerprint_file(self._repo_dir / relative_path)
            if file_digest is None:
                logger.warning("Unable to fingerprint meta cache watch file: %s", relative_path)
                return None
            digest.update(relative_path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(file_digest)
            digest.update(b"\n")

        return digest.hexdigest()

    def is_stale(self, record: MetaCacheRecord) -> bool:
        """Return True if watched file fingerprints differ from the cached record."""
        if not record.watch_files:
            return False

        if not record.watch_state_hash:
            logger.info("Meta cache record is missing watch-state fingerprint; recomputing once for migration")
            return True

        expected_watch_files = sorted(set(record.watch_files))
        discovered_watch_files = self.discover_watch_files()
        if discovered_watch_files:
            normalized_discovered = sorted(set(discovered_watch_files))
            if normalized_discovered != expected_watch_files:
                logger.info("Meta cache watch-file set changed; recomputing metadata analysis")
                return True
            expected_watch_files = normalized_discovered

        current_watch_hash = self.compute_watch_state_hash(expected_watch_files)
        if current_watch_hash is None:
            return True

        return current_watch_hash != record.watch_state_hash
