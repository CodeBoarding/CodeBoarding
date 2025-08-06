import logging
from langchain_core.prompts import PromptTemplate
from langgraph.prebuilt import create_react_agent

from agents.agent import CodeBoardingAgent
from agents.agent_responses import MetaAnalysisInsights
from agents.prompts import SYSTEM_META_ANALYSIS_MESSAGE

logger = logging.getLogger(__name__)


class MetaAgent(CodeBoardingAgent):

    def __init__(self, repo_dir, static_analysis, project_name):
        super().__init__(repo_dir, static_analysis, SYSTEM_META_ANALYSIS_MESSAGE)
        self.project_name = project_name

        self.meta_analysis_prompt = PromptTemplate(
            template="""Analyze the project '{project_name}' to understand its architectural context.

Tasks:
1. Read project documentation (README, docs) to understand the project purpose
2. Analyze file structure and requirements to identify technology stack
3. Examine dependencies and imports to understand the domain
4. Based on your expertise, classify the project type and expected architectural patterns
5. Provide guidance on how components should be organized for this project type

Use the available tools to gather this information.""",
            input_variables=["project_name"]
        )

        self.agent = create_react_agent(model=self.llm,
                                        tools=[self.read_docs, self.external_deps_tool, self.read_file_structure])

    def analyze_project_metadata(self):
        """Analyze project metadata to provide architectural context and bias."""
        logger.info(f"[MetaAgent] Analyzing metadata for project: {self.project_name}")

        prompt = self.meta_analysis_prompt.format(project_name=self.project_name)
        analysis = self._parse_invoke(prompt, MetaAnalysisInsights)

        logger.info(f"[MetaAgent] Completed metadata analysis for project: {analysis.llm_str()}")
        return analysis
