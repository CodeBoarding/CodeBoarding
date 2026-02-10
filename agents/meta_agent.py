import logging
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.prompts import PromptTemplate
from langchain_core.language_models import BaseChatModel

from agents.agent import CodeBoardingAgent
from agents.agent_responses import MetaAnalysisInsights
from agents.prompts import get_system_meta_analysis_message, get_meta_information_prompt
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


class MetaAgent(CodeBoardingAgent):

    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        project_name: str,
        agent_llm: BaseChatModel | list[BaseChatModel],
        parsing_llm: BaseChatModel | list[BaseChatModel],
    ):
        super().__init__(repo_dir, static_analysis, get_system_meta_analysis_message(), agent_llm, parsing_llm)
        self.project_name = project_name

        self.meta_analysis_prompt = PromptTemplate(
            template=get_meta_information_prompt(), input_variables=["project_name"]
        )

    def _build_agent(self, llm: BaseChatModel):
        return create_agent(
            model=llm,
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
