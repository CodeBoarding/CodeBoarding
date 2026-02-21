import logging
from typing import Optional
from langchain_core.tools import ArgsSchema
from pydantic import BaseModel
from agents.tools.base import BaseRepoTool
from agents.dependency_discovery import discover_dependency_files

logger = logging.getLogger(__name__)


class ExternalDepsInput(BaseModel):
    pass


class ExternalDepsTool(BaseRepoTool):
    name: str = "readExternalDeps"
    description: str = (
        "Scans the current repository to find common dependency files."
        "Returns a list of file paths that can be examined with the readFile tool."
    )
    args_schema: Optional[ArgsSchema] = ExternalDepsInput
    return_direct: bool = False

    def _run(self) -> str:
        """
        Run the tool to find dependency files.
        """
        logger.info("[ExternalDeps Tool] Searching for dependency files")

        discovered = discover_dependency_files(self.repo_dir, self.ignore_manager)

        if not discovered:
            logger.warning("[ExternalDeps Tool] No dependency files found in the repository.")
            return "No dependency files found in this repository. Searched for common files like requirements.txt, pyproject.toml, setup.py, environment.yml, Pipfile, etc."

        summary = f"Found {len(discovered)} dependency file(s):\n\n"

        for i, item in enumerate(discovered, 1):
            relative_path = item.path.relative_to(self.repo_dir)
            summary += f'{i}. {relative_path}\n   To read this file: Use the readFile tool with file_path="{relative_path}" and line_number=0\n\n'

        logger.info(
            f"[ExternalDeps Tool] Found {len(discovered)} dependency file(s): "
            f"{', '.join(str(d.path.relative_to(self.repo_dir)) for d in discovered)}"
        )

        return summary
