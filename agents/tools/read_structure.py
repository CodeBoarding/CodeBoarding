import os
from pathlib import Path
from typing import Optional, List

from langchain_core.tools import ArgsSchema, BaseTool

from .read_packages import PackageInput, NoRootPackageFoundError
from .utils import read_dot_file


class CodeStructureTool(BaseTool):
    name: str = "read_class_structure"
    description: str = "Tool which gives class structure relationships."
    args_schema:Optional[ArgsSchema] = PackageInput
    return_direct:bool = False
    cached_files: Optional[List[str]] = None


    def __init__(self, root_project_dir):
        super().__init__()
        self.cached_files = []
        self.walk_dir(root_project_dir)

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
        print(f"[Structure Tool] Reading structure for {root_package}")
        try:
            return self.read_file(root_package)
        except NoRootPackageFoundError as e:
            return f"Could not find  Package not found: {e.message}"

    def read_file(self, root_package: str) -> str:
        """
        Read the file from the given path.
        """

        root_package = root_package.split(".")[0]

        for path in self.cached_files:
            if root_package in path.name:
                print(f"[Structure Tool] Found file {path}")
                content = read_dot_file(path)
                return f"Package relations for: {root_package}:\n{content}"

        package_names = [path.name.split("_structure.dot")[-1] for path in self.cached_files]
        raise NoRootPackageFoundError(f"Could not find package {root_package}, available packages are: {package_names}")
