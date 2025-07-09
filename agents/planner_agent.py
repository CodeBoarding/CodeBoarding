from langchain_core.prompts import PromptTemplate
from langgraph.prebuilt import create_react_agent
from typing import List

from agents.agent import CodeBoardingAgent
from agents.agent_responses import AnalysisInsights, ExpandComponent, Component
from agents.prompts import EXPANSION_PROMPT, PLANNER_SYSTEM_MESSAGE


class PlannerAgent(CodeBoardingAgent):
    def __init__(self, repo_dir, output_dir, cfg):
        super().__init__(repo_dir, output_dir, cfg, PLANNER_SYSTEM_MESSAGE)
        self.expansion_prompt = PromptTemplate(template=EXPANSION_PROMPT, input_variables=["component"])
        self.agent = create_react_agent(model=self.llm, tools=[self.read_source_reference,
                                                               self.read_packages_tool, self.read_file_structure,
                                                               self.read_structure_tool, self.read_file_tool])

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
