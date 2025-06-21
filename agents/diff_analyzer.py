import logging
from typing import List
from agents.agent_responses import AnalysisInsights, Component, UpdateAnalysis
from agents.prompts import SYSTEM_DIFF_ANALYSIS_MESSAGE, DIFF_ANLAYSIS_MESSAGE
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from diagram_analysis.version import Version
from markdown_generation import sanitize
from repo_utils.git_diff import FileChange, get_git_diff


class DiffAnalyzingAgent(CodeBoardingAgent):
    def __init__(self, repo_dir, output_dir, cfg, project_name):
        super().__init__(repo_dir, output_dir, cfg, SYSTEM_DIFF_ANALYSIS_MESSAGE)
        self.project_name = project_name
        self.repo_dir = repo_dir
        self.prompt = PromptTemplate(
            template=DIFF_ANLAYSIS_MESSAGE,
            input_variables=["analysis", "diff_data"]
        )

    def get_anlaysis(self) -> AnalysisInsights:
        """
        Generate an initial analysis insight for the project.
        This is a placeholder method that can be overridden by subclasses.
        """
        analysis_file = self.repo_dir / ".codeboarding" / "analysis.json"  # Ensure the directory exists
        with open(analysis_file, 'r') as analysis_file:
            return AnalysisInsights.model_validate_json(analysis_file.read())

    def get_diff_data(self) -> List[FileChange]:
        """
        Get the diff data for the current repository.
        This is a placeholder method that can be overridden by subclasses.
        """
        version_file = self.repo_dir / ".codeboarding" / "codeboarding_version.json"
        with open(version_file, 'r') as f:
            version = Version.model_validate_json(f.read())
        diff = get_git_diff(self.repo_dir, version.commit_hash)
        diff = self.filter_diff(diff)
        return diff

    @staticmethod
    def filter_diff(diff: List[FileChange]) -> List[FileChange]:
        """
        Filter the diff to only include files that are relevant for analysis.
        For now, we assume that only files in the .codeboarding directory are relevant.
        """
        relevant_files = []
        for change in diff:
            # For now we are checking just python files
            if change.filename.endswith(".py"):
                relevant_files.append(change)
        return relevant_files

    def analysis_exists(self):
        """
        Check if the analysis already exists in the output directory.
        """
        codeboarding_dir = self.repo_dir / ".codeboarding"
        version_file = codeboarding_dir / "codeboarding_version.json"
        return codeboarding_dir.exists() and version_file.exists()

    def component_exists(self, component_name: str) -> bool:
        component_file = self.repo_dir / ".codeboarding" / f"{sanitize(component_name)}.json"
        return component_file.exists()

    def check_for_updates(self) -> UpdateAnalysis:
        if not self.analysis_exists():
            logging.info("[DiffAnalyzingAgent] No existing analysis found, running full analysis")
            return UpdateAnalysis(update_degree=10, message="No existing analysis found, running full analysis")

        analysis = self.get_anlaysis()
        diff_data = self.get_diff_data()
        if not diff_data:
            logging.info("[DiffAnalyzingAgent] No relevant code differences found")
            return UpdateAnalysis(update_degree=0, message="No relevant code differences found")

        logging.info("[DiffAnalyzingAgent] Analyzing code differences")
        prompt = self.prompt.format(analysis=analysis.llm_str(), diff_data="\n".join([df.llm_str() for df in diff_data]))
        update = self._parse_invoke(prompt, UpdateAnalysis)
        return update 
    
    def get_component_analysis(self, component: Component) -> AnalysisInsights:
        """
        Get the analysis for a specific component.
        This method can be overridden by subclasses to provide more specific logic.
        """
        component_file = self.repo_dir / ".codeboarding" / f"{sanitize(component.name)}.json"
        with open(component_file, 'r') as f:
            return AnalysisInsights.model_validate_json(f.read())

    def check_for_component_updates(self, component: Component) -> UpdateAnalysis:
        """
        Check if there are updates for a specific component.
        This method can be overridden by subclasses to provide more specific logic.
        """
        # Check if the component exists:
        if not self.component_exists(component.name):
            logging.info(f"[DiffAnalyzingAgent] Component {component.name} does not exist, running full analysis")
            return UpdateAnalysis(update_degree=10, message=f"Component {component.name} does not exist, running full analysis")

        diff_data = self.get_diff_data()
        if not diff_data:
            logging.info(f"[DiffAnalyzingAgent] No relevant code differences found for component {component.name}")
            return UpdateAnalysis(update_degree=0, message=f"No relevant code differences found for component {component.name}")

        analysis = self.get_component_analysis(component)
        logging.info(f"[DiffAnalyzingAgent] Analyzing code differences for component {component.name}")
        prompt = self.prompt.format(analysis=analysis.llm_str(), diff_data="\n".join([df.llm_str() for df in diff_data]))
        update = self._parse_invoke(prompt, UpdateAnalysis)
        return update