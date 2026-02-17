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

from agents.agent import CodeBoardingAgent
from agents.agent_responses import MetaAnalysisInsights
from agents.prompts import get_system_meta_analysis_message, get_meta_information_prompt
from monitoring import trace
from repo_utils import get_repo_state_hash
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)

type JsonScalar = str | int | float | bool | None


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
            tools=[self.toolkit.read_docs, self.toolkit.external_deps, self.toolkit.read_file_structure],
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

    def _meta_cache_prompt(self, repo_state_hash: str | None = None) -> str:
        """Build deterministic prompt-side cache key payload."""
        if repo_state_hash is None:
            repo_state_hash = get_repo_state_hash(self.repo_dir)
        payload = {
            "kind": "meta_agent_cache_v1",
            "project_name": self.project_name,
            "repo_state_hash": repo_state_hash,
            "prompt_version": self._meta_prompt_signature(),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def construct_cache_keys(self, repo_state_hash: str) -> tuple[str, str]:
        """Return prompt and llm cache keys for metadata context lookup."""
        return self._meta_cache_prompt(repo_state_hash), self._meta_cache_llm_string()

    def _lookup_cached_generation(self, cache: SQLiteCache, prompt_key: str, llm_key: str) -> Generation | None:
        """Lookup cached generations and normalize to expected output type."""
        raw_generations: Sequence[Generation] | None = cache.lookup(prompt_key, llm_key)
        if raw_generations is None:
            return None

        if len(raw_generations) > 1:
            logger.warning("Meta cache returned %d generations for one key; using first", len(raw_generations))

        return raw_generations[0]

    def _load_cached_meta(self, cached_generation: Generation | None) -> MetaAnalysisInsights | None:
        """Decode cached LLM generations to typed meta insights."""
        if cached_generation is None:
            return None
        text = cached_generation.text
        if not text:
            return None
        try:
            return MetaAnalysisInsights.model_validate_json(text)
        except Exception:
            return None

    def get_meta_context(
        self,
        refresh: bool = False,
    ) -> MetaAnalysisInsights:
        """Return cached metadata context or recompute and persist it."""

        repo_state_hash = get_repo_state_hash(self.repo_dir)
        if repo_state_hash == "NoRepoStateHash":
            logger.info("Meta cache disabled for non-git repository state; recomputing metadata analysis")
            return self.analyze_project_metadata()

        prompt_key, llm_key = self.construct_cache_keys(repo_state_hash, self.agent_llm, self.parsing_llm)
        cache = self._open_meta_cache()
        if cache is None:
            return self.analyze_project_metadata()

        if refresh:
            cache.clear()
            logger.info("Meta cache cleared due to refresh request")

        cached_generation = self._lookup_cached_generation(cache, prompt_key, llm_key)

        cached = self._load_cached_meta(cached_generation)
        if cached is not None:
            logger.info("Meta cache hit; reusing metadata analysis")
            return cached

        computed_meta = self.analyze_project_metadata()
        cache.update(prompt_key, llm_key, [Generation(text=computed_meta.model_dump_json())])
        return computed_meta
