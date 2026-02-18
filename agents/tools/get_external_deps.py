import logging
from typing import Optional
from langchain_core.tools import ArgsSchema
from pydantic import BaseModel
from agents.tools.base import BaseRepoTool
from agents.dependency_discovery import DEPENDENCY_FILES, discover_dependency_files

logger = logging.getLogger(__name__)


class ExternalDepsInput(BaseModel):
    """Input for ExternalDepsTool - no arguments needed."""

    pass


class ExternalDepsTool(BaseRepoTool):
    name: str = "readExternalDeps"
    description: str = (
        "Identifies project dependency files in the repository. "
        "Automatically detects common dependency files like requirements.txt, pyproject.toml, tsconfig.json, and others. "
        "Returns a list of found dependency files that can be examined with the readFile tool."
    )
    args_schema: Optional[ArgsSchema] = ExternalDepsInput
    return_direct: bool = False

    # Compatibility alias for callers that may still read this class constant.
    DEPENDENCY_FILES: tuple[str, ...] = DEPENDENCY_FILES

    def _run(self) -> str:
        """
        Run the tool to find dependency files.
        """
        logger.info("[ExternalDeps Tool] Searching for dependency files")

        found_files = discover_dependency_files(self.repo_dir, self.ignore_manager)

        if not found_files:
            logger.warning("[ExternalDeps Tool] No dependency files found in the repository.")
            return "No dependency files found in this repository. Searched for common files like requirements.txt, pyproject.toml, setup.py, environment.yml, Pipfile, etc."

        # Format the output to make it easy to use with readFile tool
        summary = f"Found {len(found_files)} dependency file(s):\n\n"

        # List files with suggestions for using readFile
        for i, file_path_item in enumerate(found_files, 1):
            relative_path = file_path_item.relative_to(self.repo_dir)
            summary += f'{i}. {relative_path}\n   To read this file: Use the readFile tool with file_path="{relative_path}" and line_number=0\n\n'

        logger.info(
            f"[ExternalDeps Tool] Found {len(found_files)} dependency file(s): {', '.join(str(f.relative_to(self.repo_dir)) for f in found_files)}"
        )

        return summary
