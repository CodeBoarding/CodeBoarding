from enum import Enum
from pathlib import Path
from typing import List

from diagram_analysis.version import Version
from repo_utils.git_diff import get_git_diff, FileChange


class UpdateResult(Enum):
    NO_UPDATE_REQUIRED = "No update required"
    PARTIAL_UPDATE_REQUIRED = "Partial update required"
    NEW_ANALYSIS = "Full update required"


class UpdateAnalyzer:
    def __init__(self, repo_dir: Path):
        self.repo_dir = repo_dir

    def update_analysis(self):
        # scan the dir if we have a .codeboarding file:
        codeboarding_dir = self.repo_dir / ".codeboarding"
        version_file = codeboarding_dir / "version.json"
        if not codeboarding_dir.exists() or not version_file.exists():
            return UpdateResult.NEW_ANALYSIS

        # now get the last version that we have:
        with open(version_file, 'r') as f:
            version = Version.model_validate_json(f.read())

        # Now do a diff of current version of the repo with the version in the file:
        diff = get_git_diff(self.repo_dir, version.commit_hash)
        diff = self.filter_diff(diff)
        if not diff:
            return UpdateResult.NO_UPDATE_REQUIRED

        diff_analyzer_agent = DiffAnalyzerAgent(codeboarding_dir)
        analysis_result = diff_analyzer_agent.analyze(diff)
        if analysis_result == UpdateResult.NO_UPDATE_REQUIRED:
            return UpdateResult.NO_UPDATE_REQUIRED
        elif analysis_result == UpdateResult.NEW_ANALYSIS:
            return UpdateResult.NEW_ANALYSIS

        # Last is if we need partial update:
        return diff_analyzer_agent.get_diff_components()

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
