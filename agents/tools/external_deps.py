import logging
from pathlib import Path
from typing import Optional, List

from langchain_core.tools import ArgsSchema, BaseTool
from pydantic import BaseModel


class ExternalDepsInput(BaseModel):
    """Input for ExternalDepsTool - no arguments needed."""
    pass


class ExternalDepsTool(BaseTool):
    name: str = "readExternalDeps"
    description: str = (
        "Reads Python project dependencies from common dependency files. "
        "Automatically detects and reads from requirements.txt, pyproject.toml, setup.py, "
        "environment.yml (conda), Pipfile, poetry.lock, and other common dependency files. "
        "Returns the contents of all found dependency files."
    )
    args_schema: Optional[ArgsSchema] = ExternalDepsInput
    return_direct: bool = False
    repo_dir: Optional[Path] = None

    # Common dependency file patterns to search for
    DEPENDENCY_FILES: List[str] = [
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-test.txt",
        "dev-requirements.txt",
        "test-requirements.txt",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "Pipfile",
        "Pipfile.lock",
        "poetry.lock",
        "environment.yml",
        "environment.yaml",
        "conda.yml",
        "conda.yaml",
        "pixi.toml",
        "uv.lock"
    ]

    def __init__(self, repo_dir: Path):
        super().__init__()
        self.repo_dir = repo_dir

    def _run(self) -> str:
        """
        Run the tool to find and read dependency files.
        """
        logging.info("[ExternalDeps Tool] Searching for dependency files")

        found_files = []
        results = []

        # Search for dependency files in the repository
        for dep_file in self.DEPENDENCY_FILES:
            file_path = self.repo_dir / dep_file
            if file_path.exists() and file_path.is_file():
                found_files.append(file_path)

        # Also search for requirements files in common subdirectories
        for subdir in ["requirements", "deps", "dependencies", "env"]:
            subdir_path = self.repo_dir / subdir
            if subdir_path.exists() and subdir_path.is_dir():
                for pattern in ["*.txt", "*.yml", "*.yaml", "*.toml"]:
                    for file_path in subdir_path.glob(pattern):
                        if file_path.is_file():
                            found_files.append(file_path)

        if not found_files:
            return "No dependency files found in this repository. Searched for common files like requirements.txt, pyproject.toml, setup.py, environment.yml, Pipfile, etc."

        # Read and format the contents of found files
        for file_path in found_files:
            relative_path = file_path.relative_to(self.repo_dir)
            try:
                with open(file_path, 'r', encoding='utf-8') as file:
                    content = file.read().strip()

                if content:
                    results.append(f"File: {relative_path}\n{'=' * 50}\n{content}\n")
                else:
                    results.append(f"File: {relative_path}\n{'=' * 50}\n(Empty file)\n")

            except Exception as e:
                results.append(f"File: {relative_path}\n{'=' * 50}\nError reading file: {str(e)}\n")

        if not results:
            return "Found dependency files but they are all empty or unreadable."

        # Add summary at the beginning
        summary = f"Found {len(found_files)} dependency file(s):\n"
        summary += "\n".join(f"- {f.relative_to(self.repo_dir)}" for f in found_files)
        summary += "\n\n" + "=" * 60 + "\n\n"

        return summary + "\n".join(results)
