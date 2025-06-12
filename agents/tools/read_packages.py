import logging
import os
from pathlib import Path
from typing import Optional, List

from langchain_core.tools import ArgsSchema, BaseTool
from pydantic import BaseModel, Field

from agents.tools.utils import read_dot_file


class PackageInput(BaseModel):
    root_package: str = Field(
        description="The top-level package name for which to retrieve dependencies. "
                    "This should be the primary name of the package, without file extensions or full paths. "
                    "For example, use 'langchain' for the `langchain` package, or 'langchain_core' "
                    "for the `langchain_core` package. Do not include 'repos.' prefix if it's present in agent's context."
    )


class NoRootPackageFoundError(Exception):
    """Custom exception for when a root package is not found."""

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class PackageRelationsTool(BaseTool):
    name: str = "getPackageDependencies"
    description: str = (
        "Retrieves the internal package dependencies for a given root package within a project. "
        "This tool is useful for understanding the hierarchical relationships and dependencies "
        "between different modules or sub-packages of a larger software component. "
        "The output is a dictionary where each key is a source package and its value is a list "
        "of destination packages it depends on, formatted as: "
        "`{<source_package>: [<destination_package1>, <destination_package2>, ...]}`."
    )
    args_schema: Optional[ArgsSchema] = PackageInput
    return_direct: bool = False
    cached_files: Optional[List[str]] = None

    def __init__(self, analysis_dir):
        super().__init__()
        self.cached_files = []
        self.walk_dir(analysis_dir)

    def walk_dir(self, root_project_dir):
        """
        Walk the directory and collect all files.
        """
        for file in os.listdir(root_project_dir):
            if file.startswith('packages_'):
                self.cached_files.append(Path(f'{root_project_dir}/{file}'))

    def _run(self, root_package: str) -> str:
        """
        Run the tool with the given input.
        """
        if root_package.startswith("repos."):
            root_package = root_package.split("repos.")[-1]
        logging.info(f"[Package Tool] Reading packages for {root_package}")
        try:
            return self.read_file(root_package)
        except NoRootPackageFoundError as e:
            logging.error(f"[Package Tool] Error: {e.message}")
            return f"Error: {e.message}"

    def read_file(self, root_package: str) -> str:
        """
        Read the file from the given path.
        """
        root_package = root_package.split(".")[0]

        for path in self.cached_files:
            if root_package in path.name:
                logging.info(f"[Package Tool] Found file {path}")
                content = read_dot_file(path)
                final_result = f"Package relations for {root_package}:\n"
                for k, v in content.items():
                    final_result += f"package {k} uses {', '.join(v)}\n"
                return final_result

        package_names = [path.name.split("packages_")[-1].split(".dot")[0] for path in self.cached_files]
        raise NoRootPackageFoundError(
            f"Failed to retrieve dependencies for package '{root_package}'. It was not found. Please choose from the available packages: {package_names}")
