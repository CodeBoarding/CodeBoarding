import hashlib
import json
import logging
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from langchain_community.cache import SQLiteCache
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import Generation
from langchain_core.prompts import PromptTemplate
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from agents.agent import CodeBoardingAgent
from agents.agent_responses import MetaAnalysisInsights
from agents.prompts import get_system_meta_analysis_message, get_meta_information_prompt
from agents.dependency_discovery import discover_dependency_files
from monitoring import trace
from repo_utils import NO_COMMIT_HASH, Repo, get_git_commit_hash, require_git_import
from repo_utils.change_detector import detect_changes_from_commit, detect_uncommitted_changes
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)

type JsonScalar = str | int | float | bool | None


class MetaCacheRecord(BaseModel):
    meta: MetaAnalysisInsights
    base_commit: str
    dep_files: list[str]


class MetaAgent(CodeBoardingAgent):

    def __init__(
        self,
        repo_dir: Path,
        project_name: str,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
    ):
        super().__init__(repo_dir, StaticAnalysisResults(), get_system_meta_analysis_message(), agent_llm, parsing_llm)
        self.project_name = project_name
        self.agent_llm = agent_llm

        self.meta_analysis_prompt = PromptTemplate(
            template=get_meta_information_prompt(), input_variables=["project_name"]
        )

        self.agent = create_react_agent(
            model=agent_llm,
            tools=[
                self.toolkit.read_docs,
                self.toolkit.read_file,
                self.toolkit.external_deps,
                self.toolkit.read_file_structure,
            ],
        )

    @trace
    def analyze_project_metadata(self) -> MetaAnalysisInsights:
        """Analyze project metadata to provide architectural context and bias."""
        logger.info(f"[MetaAgent] Analyzing metadata for project: {self.project_name}")

        prompt = self.meta_analysis_prompt.format(project_name=self.project_name)
        analysis = self._parse_invoke(prompt, MetaAnalysisInsights)

        logger.info(f"[MetaAgent] Completed metadata analysis for project: {analysis.llm_str()}")
        return analysis

    def _meta_cache_path(self) -> Path:
        """Return repository-local SQLite path for MetaAgent cache."""
        cache_dir = self.repo_dir / ".codeboarding" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "meta_agent_llm.sqlite"

    def _open_meta_cache(self) -> SQLiteCache | None:
        """Open sqlite cache, returning None when cache cannot be used."""
        try:
            db_path = self._meta_cache_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            return SQLiteCache(database_path=str(db_path))
        except (OSError, sqlite3.Error) as e:
            logger.warning("Meta cache disabled: %s", e)
            return None

    def _meta_prompt_signature(self) -> str:
        """Return stable prompt version hash for cache invalidation on prompt changes."""
        prompt_material = get_system_meta_analysis_message() + "\n" + get_meta_information_prompt()
        return hashlib.sha256(prompt_material.encode("utf-8")).hexdigest()

    def _llm_signature(self, llm: BaseChatModel) -> str:
        """Build a stable model signature string used in cache keys."""
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

    def _meta_cache_llm_string(self) -> str:
        """Build cache key payload that includes both agent and parsing model signatures."""
        payload = {
            "kind": "meta_agent_llm_cache_v2",
            "agent": self._llm_signature(self.agent_llm),
            "parser": self._llm_signature(self.parsing_llm),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def _meta_cache_prompt(self) -> str:
        """Build deterministic prompt-side cache key payload."""
        payload = {
            "kind": "meta_agent_cache_v2",
            "project_name": self.project_name,
            "prompt_version": self._meta_prompt_signature(),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def construct_cache_keys(self) -> tuple[str, str]:
        """Return prompt and llm cache keys for metadata context lookup."""
        return self._meta_cache_prompt(), self._meta_cache_llm_string()

    def _lookup_cached_generation(self, cache: SQLiteCache, prompt_key: str, llm_key: str) -> Generation | None:
        """Lookup cached generations and normalize to expected output type."""
        raw_generations: Sequence[Generation] | None = cache.lookup(prompt_key, llm_key)
        if raw_generations is None:
            return None

        if len(raw_generations) > 1:
            logger.warning("Meta cache returned %d generations for one key; using first", len(raw_generations))

        return raw_generations[0]

    def _load_cached_record(self, cached_generation: Generation | None) -> MetaCacheRecord | None:
        """Decode cached generations into typed metadata cache records."""
        if cached_generation is None:
            return None
        text = cached_generation.text
        if not text:
            return None
        try:
            return MetaCacheRecord.model_validate_json(text)
        except Exception:
            return None

    def _save_cached_record(
        self,
        cache: SQLiteCache,
        prompt_key: str,
        llm_key: str,
        meta: MetaAnalysisInsights,
        base_commit: str,
        dep_files: list[str],
    ) -> None:
        """Persist a metadata cache record envelope."""
        record = MetaCacheRecord(meta=meta, base_commit=base_commit, dep_files=dep_files)
        cache.update(prompt_key, llm_key, [Generation(text=record.model_dump_json())])

    def _changes_intersect_dep_files(self, changed_paths: set[str], dep_files: set[str]) -> bool:
        """Return True when any changed file path intersects the watched dependency files."""
        return bool(changed_paths & dep_files)

    @require_git_import(default=[])
    def _discover_dep_files(self) -> list[str]:
        """Discover tracked dependency files relevant for metadata invalidation."""
        try:
            repo = Repo(self.repo_dir)
            tracked_files = set(repo.git.ls_files().splitlines())
        except Exception as e:
            logger.warning("Unable to discover tracked files for meta cache dependencies: %s", e)
            return []

        dep_files: set[str] = set()
        for file_path in discover_dependency_files(self.repo_dir, self.ignore_manager):
            relative_path = file_path.relative_to(self.repo_dir).as_posix()
            if relative_path in tracked_files:
                dep_files.add(relative_path)

        return sorted(dep_files)

    @require_git_import(default=True)
    def _deps_changed_since(self, base_commit: str, dep_files: list[str]) -> bool:
        """Return True if tracked dependency files changed since base_commit."""
        if not dep_files:
            return False
        if not base_commit:
            return True

        dep_files_set = set(dep_files)
        try:
            committed_changes = detect_changes_from_commit(self.repo_dir, base_commit)
            committed_paths = committed_changes.all_affected_files | committed_changes.all_old_paths
            if bool(committed_paths & dep_files_set):
                return True

            uncommitted_changes = detect_uncommitted_changes(self.repo_dir)
            uncommitted_paths = uncommitted_changes.all_affected_files | uncommitted_changes.all_old_paths
            if bool(uncommitted_paths & dep_files_set):
                return True
        except Exception as e:
            logger.warning("Failed to evaluate dependency diff for meta cache: %s", e)
            return True

        return False

    def get_meta_context(
        self,
        refresh: bool = False,
    ) -> MetaAnalysisInsights:
        """Return cached metadata context or recompute and persist it."""
        cache = self._open_meta_cache()

        if cache is None:
            logger.info("Meta cache unavailable; recomputing metadata analysis")
            return self.analyze_project_metadata()

        if refresh:
            cache.clear()
            logger.info("Meta cache cleared due to refresh request")

        prompt_key, llm_key = self.construct_cache_keys()
        cached_generation = self._lookup_cached_generation(cache, prompt_key, llm_key)
        cached_record = self._load_cached_record(cached_generation)
        if cached_record is None:
            logger.info("Meta cache miss; recomputing metadata analysis")
        else:
            if not self._deps_changed_since(cached_record.base_commit, cached_record.dep_files):
                logger.info("Meta cache hit; reusing metadata analysis")
                return cached_record.meta
            logger.info("Meta cache invalidated by dependency changes; recomputing metadata analysis")

        computed_meta = self.analyze_project_metadata()
        base_commit = get_git_commit_hash(str(self.repo_dir))
        if base_commit == NO_COMMIT_HASH:
            logger.warning("Unable to resolve current commit for meta cache")
            base_commit = ""
        dep_files = self._discover_dep_files()
        self._save_cached_record(
            cache=cache,
            prompt_key=prompt_key,
            llm_key=llm_key,
            meta=computed_meta,
            base_commit=base_commit,
            dep_files=dep_files,
        )
        return computed_meta
