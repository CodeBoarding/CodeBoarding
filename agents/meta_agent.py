import logging
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate
from langgraph.prebuilt import create_react_agent

from agents.agent import CodeBoardingAgent
from agents.agent_responses import MetaAnalysisInsights
from agents.prompts import get_system_meta_analysis_message, get_meta_information_prompt
from cache.meta_cache import MetaAgentCache, build_meta_snapshot
from monitoring import trace
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

    def get_meta_context(
        self,
        force_refresh: bool = False,
        agent_llm: BaseChatModel | None = None,
    ) -> MetaAnalysisInsights:
        """Return cached metadata context or recompute and persist it."""
        effective_llm = agent_llm or self.agent_llm
        snapshot = build_meta_snapshot(self.repo_dir, effective_llm)
        cache = MetaAgentCache.from_repo_dir(self.repo_dir)

        if not force_refresh:
            cached_meta = cache.load_if_valid(snapshot)
            if cached_meta is not None:
                logger.info("Meta cache hit; reusing metadata analysis")
                return cached_meta
            logger.info("Meta cache miss/invalid; recomputing metadata analysis")
        else:
            logger.info("Meta cache bypassed due to force_refresh; recomputing metadata analysis")

        computed_meta = self.analyze_project_metadata()
        cache.save(snapshot, computed_meta)
        return computed_meta
