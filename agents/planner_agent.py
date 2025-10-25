from pathlib import Path
from typing import List

from langchain_core.prompts import PromptTemplate
from langgraph.prebuilt import create_react_agent

from agents.agent import CodeBoardingAgent
from agents.agent_responses import AnalysisInsights, ExpandComponent, Component
from agents.prompts import get_expansion_prompt, get_planner_system_message
from agents.monitoring import monitoring
from static_analyzer.analysis_result import StaticAnalysisResults


class PlannerAgent(CodeBoardingAgent):
    def __init__(self, repo_dir: Path, static_analysis: StaticAnalysisResults):
        super().__init__(repo_dir, static_analysis, get_planner_system_message())
        self.expansion_prompt = PromptTemplate(template=get_expansion_prompt(), input_variables=["component"])
        self.agent = create_react_agent(model=self.llm, tools=[self.read_source_reference,
                                                               self.read_packages_tool, self.read_file_structure,
                                                               self.read_structure_tool, self.read_file_tool])

    @monitoring
    def plan_analysis(self, analysis: AnalysisInsights) -> List[Component]:
        """
        Generate a plan for analyzing the provided components.
        This method should return a structured plan detailing how to analyze each component.
        """
        expandable_components = []
        for component in analysis.components:
            response = self._parse_invoke(self.expansion_prompt.format(component=component.llm_str()), ExpandComponent)
            if response.should_expand:
                expandable_components.append(component)
        return expandable_components
