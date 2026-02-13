import copy
import hashlib
import json
import logging
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

    def _meta_prompt_version(self) -> str:
        """Return stable prompt version hash for cache invalidation on prompt changes."""
        prompt_material = get_system_meta_analysis_message() + "\n" + get_meta_information_prompt()
        return hashlib.sha256(prompt_material.encode("utf-8")).hexdigest()

    def _meta_llm_string(self, effective_llm: BaseChatModel) -> str:
        """Build a stable model signature string used in cache keys."""
        model_id = None
        for attr in ("model_name", "model", "model_id"):
            value = getattr(effective_llm, attr, None)
            if isinstance(value, str) and value:
                model_id = value
                break

        config: dict[str, object] = {}
        for attr in ("temperature", "max_tokens", "top_p", "timeout", "max_retries"):
            value = getattr(effective_llm, attr, None)
            if isinstance(value, (str, int, float, bool)) or value is None:
                config[attr] = value

        payload = {
            "provider": f"{type(effective_llm).__module__}.{type(effective_llm).__name__}",
            "model_id": model_id or type(effective_llm).__name__,
            "config": config,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def _meta_cache_prompt(self) -> str:
        """Build deterministic prompt-side cache key payload."""
        payload = {
            "kind": "meta_agent_cache_v1",
            "project_name": self.project_name,
            "repo_state_hash": get_repo_state_hash(self.repo_dir),
            "prompt_version": self._meta_prompt_version(),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def _load_cached_meta(self, cached_generations: object) -> MetaAnalysisInsights | None:
        """Decode cached LLM generations to typed meta insights."""
        if not isinstance(cached_generations, list) or not cached_generations:
            return None
        first = cached_generations[0]
        text = getattr(first, "text", None)
        if not isinstance(text, str) or not text:
            return None
        try:
            return MetaAnalysisInsights.model_validate_json(text)
        except Exception:
            return None

    def get_meta_context(
        self,
        force_refresh: bool = False,
        agent_llm: BaseChatModel | None = None,
    ) -> MetaAnalysisInsights:
        """Return cached metadata context or recompute and persist it."""
        effective_llm = copy.copy(agent_llm or self.agent_llm)
        cache = SQLiteCache(database_path=str(self._meta_cache_path()))
        prompt_key = self._meta_cache_prompt()
        llm_string = self._meta_llm_string(effective_llm)

        if force_refresh:
            try:
                cache.clear()
                logger.info("Meta cache cleared due to force_refresh")
            except Exception as e:
                logger.warning("Failed clearing meta cache; continuing with recompute: %s", e)

        try:
            cached_generations = cache.lookup(prompt_key, llm_string)
        except Exception as e:
            logger.warning("Meta cache lookup failed; treating as cache miss: %s", e)
            cached_generations = None

        cached = self._load_cached_meta(cached_generations)
        if cached is not None:
            logger.info("Meta cache hit; reusing metadata analysis")
            return cached

        computed_meta = self.analyze_project_metadata()
        try:
            cache.update(prompt_key, llm_string, [Generation(text=computed_meta.model_dump_json())])
        except Exception as e:
            logger.warning("Meta cache update failed; continuing without cache write: %s", e)
        return computed_meta
