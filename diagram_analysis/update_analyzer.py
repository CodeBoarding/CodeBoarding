from enum import Enum
from pathlib import Path
from typing import List

from agents.agent_responses import AnalysisInsights, UpdateAnalysis, ValidationInsights
from agents.diff_analyzer import DiffAnalyzingAgent
from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.version import Version
from repo_utils.git_diff import get_git_diff, FileChange
from utils import create_temp_repo_folder


class UpdateResult(Enum):
    NO_UPDATE_REQUIRED = "No update required"
    PARTIAL_UPDATE_REQUIRED = "Partial update required"
    NEW_ANALYSIS = "Full update required"


class UpdateHandler:
    def __init__(self, repo_dir: Path, repo_name: str):
        self.repo_dir = repo_dir
        self.repo_name = repo_name
        self.new_files = []

        temp_folder = create_temp_repo_folder()
        self.generator = DiagramGenerator(repo_location=self.repo_dir, temp_folder=temp_folder, output_location=temp_folder, repo_name=self.repo_name, depth_level=1)


    def update_analysis(self):
        # scan the dir if we have a .codeboarding file:
        codeboarding_dir = self.repo_dir / ".codeboarding"
        version_file = codeboarding_dir / "codeboarding_version.json"
        if not codeboarding_dir.exists() or not version_file.exists():
            return self.run_full_analysis(), UpdateResult.NEW_ANALYSIS

        # now get the last version that we have:
        with open(version_file, 'r') as f:
            version = Version.model_validate_json(f.read())

        # Now do a diff of current version of the repo with the version in the file:
        diff = get_git_diff(self.repo_dir, version.commit_hash)
        diff = self.filter_diff(diff)
        if not diff:
            return [], UpdateResult.NO_UPDATE_REQUIRED

        diff_analyzer_agent = DiffAnalyzingAgent(
            repo_dir=self.repo_dir,
            output_dir=codeboarding_dir,
            cfg=None,  # Assuming cfg is not needed for this context
            project_name=self.repo_dir.name
        )

        self.generator.pre_analysis()

        for analysis_file in self.get_analysis_files(codeboarding_dir):
            with open(analysis_file, 'r') as f:
                analysis = AnalysisInsights.model_validate_json(f.read())

            result = diff_analyzer_agent.check_for_updates(diff, analysis)
            if result.update_degree < 5:
                self.new_files.append(analysis_file)
                continue
            if result.update_degree >= 9 and analysis_file.endswith("analysis.json"):
                return self.run_full_analysis()
            # Now we do partial analysis:
            if analysis_file.endswith("analysis.json"):
                # We use the analysis agent
                return self.run_partial_analysis(analysis, result)
            else:
                self.new_files.extend(self.run_details_analysis(analysis, diff_analyzer_agent))

    def run_full_analysis(self) -> UpdateResult:
        self.new_files = self.generator.generate_analysis()
        return self.new_files

    def run_partial_analysis(self, analysis: AnalysisInsights, update_analysis: UpdateAnalysis):
        # TODO: Think how this will work with the new analysis...
        validation_insight = ValidationInsights(is_valid=update_analysis.update_degree < 5, additional_info=update_analysis.feedback)
        assert not validation_insight.is_valid, "Validation should be invalid for partial analysis"
        new_analysis = self.generator.abstraction_agent.apply_feedback(analysis, validation_insight)
        # Now check if we need to do extra generations:
        ogirinal_component_names = [c.name for c in analysis.components]
        for component in new_analysis.components:
            if component.name not in ogirinal_component_names:
                # This is a new component, we need to analyze it
                self.new_files.extend(self.generator.process_component(component, new_analysis))
        return self.new_files

    def run_details_analysis(self, analysis: AnalysisInsights, update_analysis: UpdateAnalysis) -> UpdateResult:
        # TODO: Rethink how this will work with the new analysis...
        validation_insight = ValidationInsights(is_valid=update_analysis.update_degree < 5, additional_info=update_analysis.feedback)
        assert not validation_insight.is_valid, "Validation should be invalid for details analysis"
        new_analysis = self.generator.details_agent.apply_feedback(analysis, validation_insight)
        # Now check if we need to do extra generations:
        return ""


    @staticmethod
    def get_analysis_files(codeboarding_dir: Path) -> List[Path]:
        """
        Get all analysis files in the .codeboarding directory.
        """
        files =  list(codeboarding_dir.glob("*.json"))
        return [f for f in files if "codeboarding_version.json" not in f.name]

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
