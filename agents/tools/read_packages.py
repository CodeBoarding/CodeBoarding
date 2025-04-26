from typing import Optional, List

from langchain_core.tools import ArgsSchema, BaseTool
from pydantic import BaseModel, Field


class PackageInput(BaseModel):
    root_package: str = Field(
        description="Root level package name. Example: langchain or langchain_core")


class NoRootPackageFoundError(Exception):
    """Custom exception for when a root package is not found."""

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)

class PackageRelationsTool(BaseTool):
    name: str = "package_relations"
    description: str = ("Tool which can give package relationships for a root package. "
                        "It gives the root pacakge and its relationships to other root packages as well as it's children packages.")
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
        for path in root_project_dir.rglob('*.dot'):
            if path.name.startswith('packages_'):
                self.cached_files.append(path)

    def _run(self, root_package: str) -> str:
        """
        Run the tool with the given input.
        """
        print(f"[Package Tool] Reading packages for {root_package}")
        try:
            return self.read_file(root_package)
        except NoRootPackageFoundError as e:
            return f"Could not find  Package not found: {e.message}"

    def read_file(self, root_package: str) -> str:
        """
        Read the file from the given path.
        """
        for path in self.cached_files:
            if root_package in path.name:
                with open(path, 'r') as f:
                    return f"Package relations for: {root_package}:\n{f.read()}"

        package_names = [path.name.split("packages_")[-1].split(".dot")[0] for path in self.cached_files]
        raise NoRootPackageFoundError(f"Could not find package {root_package}, available packages are: {package_names}")
