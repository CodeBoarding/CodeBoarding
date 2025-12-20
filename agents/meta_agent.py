import logging
from pathlib import Path

from langchain_core.prompts import PromptTemplate
from langgraph.prebuilt import create_react_agent

from agents.agent import LargeModelAgent
from agents.agent_responses import MetaAnalysisInsights
from agents.prompts import get_system_meta_analysis_message, get_meta_information_prompt
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


class MetaAgent(LargeModelAgent):

    def __init__(self, repo_dir: Path, static_analysis: StaticAnalysisResults, project_name: str):
        super().__init__(repo_dir, static_analysis, get_system_meta_analysis_message())
        self.project_name = project_name

        self.meta_analysis_prompt = PromptTemplate(
            template=get_meta_information_prompt(), input_variables=["project_name"]
        )

        self.agent = create_react_agent(
            model=self.llm, tools=[self.read_docs, self.external_deps_tool, self.read_file_structure]
        )

    @trace
    def analyze_project_metadata(self) -> MetaAnalysisInsights:
        """Analyze project metadata to provide architectural context and bias."""
        logger.info(f"[MetaAgent] Analyzing metadata for project: {self.project_name}")

        prompt = self.meta_analysis_prompt.format(project_name=self.project_name)
        analysis = self._parse_invoke(prompt, MetaAnalysisInsights)

        logger.info(f"[MetaAgent] Completed metadata analysis for project: {analysis.llm_str()}")
        return analysis
