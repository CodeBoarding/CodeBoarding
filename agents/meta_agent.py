import logging
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate
from langchain.agents import create_agent

from agents.agent import CodeBoardingAgent
from agents.agent_responses import MetaAnalysisInsights
from agents.prompts import get_system_meta_analysis_message, get_meta_information_prompt
from caching.meta_cache import MetaCache
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

        self.agent = create_agent(
            model=agent_llm,
            tools=[
                self.toolkit.read_docs,
                self.toolkit.read_file,
                self.toolkit.external_deps,
                self.toolkit.read_file_structure,
            ],
        )

        self._meta_cache = MetaCache(repo_dir=repo_dir, ignore_manager=self.ignore_manager)

    @trace
    def analyze_project_metadata(self) -> MetaAnalysisInsights:
        """Analyze project metadata to provide architectural context and bias."""
        logger.info(f"[MetaAgent] Analyzing metadata for project: {self.project_name}")

        prompt = self.meta_analysis_prompt.format(project_name=self.project_name)
        if (cached := self._meta_cache.load(prompt)) is not None:
            return cached
        analysis = self._parse_invoke(prompt, MetaAnalysisInsights)
        self._meta_cache.store(prompt, analysis)

        logger.info(f"[MetaAgent] Completed metadata analysis for project: {analysis.llm_str()}")
        return analysis
