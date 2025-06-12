import logging
import os
from pathlib import Path
from typing import Optional, List

from langchain_core.tools import ArgsSchema, BaseTool

from .read_packages import PackageInput, NoRootPackageFoundError
from .utils import read_dot_file


class CodeStructureTool(BaseTool):
    name: str = "getClassHierarchy"
    description: str = (
        "Retrieves the internal class structure and hierarchy for a given root package within a project. "
        "This tool is useful for understanding how classes are organized, inherit from each other, "
        "and relate within a specific software package or module. "
        "The output is a detailed representation (e.g., a DOT graph format string) illustrating "
        "these class relationships and their hierarchy. "
        "It helps to visualize the internal architecture of a package."
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
            if file.endswith('_structure.dot'):
                self.cached_files.append(Path(f'{root_project_dir}/{file}'))

    def _run(self, root_package: str) -> str:
        """
        Run the tool with the given input.
        """
        if root_package.startswith("repos."):
            root_package = root_package.split("repos.")[-1]
        logging.info(f"[Structure Tool] Reading structure for {root_package}")
        try:
            return self.read_file(root_package)
        except NoRootPackageFoundError as e:
            return f"Error: {e.message}"

    def read_file(self, root_package: str) -> str:
        """
        Read the file from the given path.
        """

        root_package = root_package.split(".")[0]

        for path in self.cached_files:
            if root_package in path.name:
                logging.info(f"[Structure Tool] Found file {path}")
                content = read_dot_file(path)
                return f"Package relations for: {root_package}:\n{content}"

        package_names = [path.name.split("_structure.dot")[-1] for path in self.cached_files]
        raise NoRootPackageFoundError(
            f"Class structure for package '{root_package}' not found. Available packages with structure information: {package_names}")
