import logging
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate
from langchain.agents import create_agent

from agents.agent import CodeBoardingAgent
from agents.agent_responses import MetaAnalysisInsights
from agents.prompts import get_system_meta_analysis_message, get_meta_information_prompt
from caching.meta_cache import MetaCache, MetaCacheKey
from monitoring import trace
from repo_utils import NO_COMMIT_HASH, get_git_commit_hash
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

        self.agent = create_agent(
            model=agent_llm,
            tools=[
                self.toolkit.read_docs,
                self.toolkit.read_file,
                self.toolkit.external_deps,
                self.toolkit.read_file_structure,
            ],
        )

        prompt_material = get_system_meta_analysis_message() + "\n" + get_meta_information_prompt()
        self._cache = MetaCache(
            repo_dir=repo_dir,
            ignore_manager=self.ignore_manager,
            project_name=project_name,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
            prompt_material=prompt_material,
        )

    @trace
    def analyze_project_metadata(self) -> MetaAnalysisInsights:
        """Analyze project metadata to provide architectural context and bias."""
        logger.info(f"[MetaAgent] Analyzing metadata for project: {self.project_name}")

        prompt = self.meta_analysis_prompt.format(project_name=self.project_name)
        analysis = self._parse_invoke(prompt, MetaAnalysisInsights)

        logger.info(f"[MetaAgent] Completed metadata analysis for project: {analysis.llm_str()}")
        return analysis

    def get_meta_context(self, refresh: bool = False) -> MetaAnalysisInsights:
        """Return cached metadata context or recompute and persist it."""
        if refresh:
            self._cache.clear()
            logger.info("Meta cache cleared due to refresh request")

        record = self._cache.load()

        if record is not None:
            if not self._cache.is_stale(record):
                logger.info("Meta cache hit; reusing metadata analysis")
                return record.meta
            logger.info("Meta cache invalidated by watch-file changes; recomputing metadata analysis")
        else:
            logger.info("Meta cache miss; recomputing metadata analysis")

        computed_meta = self.analyze_project_metadata()

        watch_files = self._cache.discover_metadata_files()
        watch_state_hash = self._cache._compute_metadata_content_hash(watch_files)
        base_commit = get_git_commit_hash(str(self.repo_dir))

        if not watch_files:
            logger.warning("No watch files found for meta cache")
        elif watch_state_hash is None:
            logger.warning("Unable to fingerprint watch files for meta cache")
        else:
            if base_commit == NO_COMMIT_HASH:
                logger.warning("Unable to resolve current commit for meta cache; storing fingerprint-only record")
            self._cache.store(
                MetaCacheKey(
                    meta=computed_meta,
                    base_commit=base_commit,
                    watch_files=watch_files,
                    watch_state_hash=watch_state_hash,
                )
            )

        return computed_meta
