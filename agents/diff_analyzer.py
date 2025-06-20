import logging
from typing import List
from agents.agent_responses import AnalysisInsights, UpdateAnalysis
from agents.prompts import SYSTEM_DIFF_ANALYSIS_MESSAGE, DIFF_ANLAYSIS_MESSAGE
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from repo_utils.git_diff import FileChange


class DiffAnalyzingAgent(CodeBoardingAgent):
    def __init__(self, repo_dir, output_dir, cfg, project_name):
        super().__init__(repo_dir, output_dir, cfg, SYSTEM_DIFF_ANALYSIS_MESSAGE)
        self.project_name = project_name
        self.prompt = PromptTemplate(
            template=DIFF_ANLAYSIS_MESSAGE,
            input_variables=["analysis", "diff_data"]
        )

    def check_for_updates(self, diff_data: List[FileChange], analysis: AnalysisInsights) -> UpdateAnalysis:
        logging.info("[DiffAnalyzingAgent] Analyzing code differences")
        prompt = self.prompt.format(analysis=analysis.llm_str(), diff_data="\n".join([df.llm_str() for df in diff_data]))
        update = self._parse_invoke(prompt, UpdateAnalysis)
        return update 