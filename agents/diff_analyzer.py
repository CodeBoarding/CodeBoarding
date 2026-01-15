import logging
from pathlib import Path
from typing import List

from langchain_core.prompts import PromptTemplate
from langgraph.prebuilt import create_react_agent

from agents.agent import LargeModelAgent
from agents.agent_responses import AnalysisInsights, Component, UpdateAnalysis
from agents.prompts import get_system_diff_analysis_message, get_diff_analysis_message
from monitoring import trace
from diagram_analysis.version import Version
from static_analyzer.analysis_result import StaticAnalysisResults
from utils import get_config

logger = logging.getLogger(__name__)
from output_generators.markdown import sanitize
from repo_utils.git_diff import FileChange, get_git_diff


class DiffAnalyzingAgent(LargeModelAgent):
    def __init__(self, repo_dir: Path, static_analysis: StaticAnalysisResults, project_name: str):
        super().__init__(repo_dir, static_analysis, get_system_diff_analysis_message())
        self.project_name = project_name
        self.repo_dir = repo_dir
        self.supported_extensions = self._get_supported_extensions()

        logger.info(f"[DiffAnalyzingAgent] Monitoring changes for extensions: {sorted(self.supported_extensions)}")

        self.prompt = PromptTemplate(template=get_diff_analysis_message(), input_variables=["analysis", "diff_data"])
        self.read_diff_tool = self.toolkit.get_read_diff_tool(diffs=self.get_diff_data())

        tools = self.toolkit.get_agent_tools()
        tools.append(self.read_diff_tool)

        self.agent = create_react_agent(
            model=self.llm,
            tools=tools,
        )

    def get_analysis(self) -> AnalysisInsights:
        """
        Generate an initial analysis insight for the project.
        This is a placeholder method that can be overridden by subclasses.
        """
        analysis_file_path = self.repo_dir / ".codeboarding" / "analysis.json"  # Ensure the directory exists
        with open(analysis_file_path, "r") as analysis_file:
            return AnalysisInsights.model_validate_json(analysis_file.read())

    def _get_supported_extensions(self) -> set[str]:
        """
        Extract supported file extensions from static analysis config.

        Returns:
            Set of supported file extensions (e.g., {'.py', '.ts', '.go'})
        """
        extensions = set()
        lsp_servers = get_config("lsp_servers")
        for server_config in lsp_servers.values():
            extensions.update(server_config.get("file_extensions", []))
        return extensions

    def get_diff_data(self) -> List[FileChange]:
        """
        Get the diff data for the current repository.
        This is a placeholder method that can be overridden by subclasses.
        """
        version_file = self.repo_dir / ".codeboarding" / "codeboarding_version.json"
        if not version_file.exists():
            logger.warning("[DiffAnalyzingAgent] No version file found, cannot get diff data")
            return []
        with open(version_file, "r") as f:
            version = Version.model_validate_json(f.read())
        diff = get_git_diff(self.repo_dir, version.commit_hash)
        diff = self.filter_diff(diff)
        return diff

    def filter_diff(self, diff: List[FileChange]) -> List[FileChange]:
        """
        Filter the diff to only include files with supported extensions.

        Args:
            diff: List of file changes from git

        Returns:
            List of file changes with supported extensions
        """
        relevant_files = []
        skipped_files = []
        for change in diff:
            file_ext = Path(change.filename).suffix
            if file_ext in self.supported_extensions:
                relevant_files.append(change)
            else:
                skipped_files.append(change.filename)

        if skipped_files:
            self._log_skipped_files(skipped_files)

        filtered_count = len(diff) - len(relevant_files)
        if filtered_count > 0:
            logger.debug(f"[DiffAnalyzingAgent] Filtered out {filtered_count} unsupported files")

        return relevant_files

    def _log_skipped_files(self, skipped_files: list[str]):
        # Configuration for truncation
        MAX_FILES_LIMIT = 10
        MAX_CHAR_LIMIT = 300

        visible_files = []
        current_length = 0
        truncated_count = 0
        for i, filename in enumerate(skipped_files):
            # Check if adding this filename (plus a comma/space) exceeds our limits
            if (current_length + len(filename) + 2) > MAX_CHAR_LIMIT or i >= MAX_FILES_LIMIT:
                truncated_count = len(skipped_files) - i
                break

            visible_files.append(filename)
            current_length += len(filename) + 2  # +2 for the comma and space

        # Construct the final message
        log_list = ", ".join(visible_files)
        suffix = f" ... (and {truncated_count} more)" if truncated_count > 0 else ""

        logger.debug(f"[DiffAnalyzingAgent] Filtered {len(skipped_files)} unsupported files: " f"[{log_list}{suffix}]")

    def analysis_exists(self):
        """
        Check if the analysis already exists in the output directory.
        """
        codeboarding_dir = self.repo_dir / ".codeboarding"
        version_file = codeboarding_dir / "codeboarding_version.json"
        analysis_file = codeboarding_dir / "analysis.json"

        return codeboarding_dir.exists() and version_file.exists() and analysis_file.exists()

    def component_exists(self, component_name: str) -> bool:
        component_file = self.repo_dir / ".codeboarding" / f"{sanitize(component_name)}.json"
        return component_file.exists()

    @trace
    def check_for_updates(self) -> UpdateAnalysis:
        if not self.analysis_exists():
            logger.info("[DiffAnalyzingAgent] No existing analysis found, running full analysis")
            return UpdateAnalysis(update_degree=10, feedback="No existing analysis found, running full analysis")

        analysis = self.get_analysis()
        diff_data = self.get_diff_data()
        if not diff_data:
            logger.info("[DiffAnalyzingAgent] No relevant code differences found")
            return UpdateAnalysis(update_degree=0, feedback="No relevant code differences found")

        logger.info("[DiffAnalyzingAgent] Analyzing code differences")
        prompt = self.prompt.format(
            analysis=analysis.llm_str(), diff_data="\n".join([df.llm_str() for df in diff_data])
        )
        update = self._parse_invoke(prompt, UpdateAnalysis)
        logger.info(f"[DiffAnalyzingAgent] Update degree: {update.update_degree}, Feedback: {update.feedback}")
        return update

    def get_component_analysis(self, component: Component) -> AnalysisInsights:
        """
        Get the analysis for a specific component.
        This method can be overridden by subclasses to provide more specific logic.
        """
        component_file = self.repo_dir / ".codeboarding" / f"{sanitize(component.name)}.json"
        with open(component_file, "r") as f:
            return AnalysisInsights.model_validate_json(f.read())

    @trace
    def check_for_component_updates(self, component: Component) -> UpdateAnalysis:
        """
        Check if there are updates for a specific component.
        This method can be overridden by subclasses to provide more specific logic.
        """
        # Check if the component exists:
        if not self.component_exists(component.name):
            logger.info(f"[DiffAnalyzingAgent] Component {component.name} does not exist, running full analysis")
            return UpdateAnalysis(
                update_degree=10, feedback=f"Component {component.name} does not exist, running full analysis"
            )

        diff_data = self.get_diff_data()
        if not diff_data:
            logger.info(f"[DiffAnalyzingAgent] No relevant code differences found for component {component.name}")
            return UpdateAnalysis(
                update_degree=0, feedback=f"No relevant code differences found for component {component.name}"
            )

        analysis = self.get_component_analysis(component)
        logger.info(f"[DiffAnalyzingAgent] Analyzing code differences for component {component.name}")
        prompt = self.prompt.format(
            analysis=analysis.llm_str(), diff_data="\n".join([df.llm_str() for df in diff_data])
        )
        update = self._parse_invoke(prompt, UpdateAnalysis)
        logger.info(
            f"[DiffAnalyzingAgent] Update degree for component {component.name}: {update.update_degree}, Feedback: {update.feedback}"
        )
        return update
