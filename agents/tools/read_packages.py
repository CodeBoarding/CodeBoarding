import logging
import os
from pathlib import Path
from typing import Optional, List, Dict, Tuple

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
    line: int = Field(
        default=0,
        description="The starting line number for pagination. Use this to request the next chunk of results if the output was truncated."
    )


class NoRootPackageFoundError(Exception):
    """Custom exception for when a root package is not found."""

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class PackageRelationsTool(BaseTool):
    name: str = "getPackageDependencies"
    description: str = (
        "Retrieves package dependencies for a root package. "
        "**HIGH-LEVEL USE ONLY** - Use once per analysis to understand main package structure. "
        "Shows hierarchical relationships between modules and sub-packages. "
        "**CONSTRAINT**: Use only for primary project packages, not for detailed exploration. "
        "Prefer analyzing CFG data before using this tool."
    )
    args_schema: Optional[ArgsSchema] = PackageInput
    return_direct: bool = False
    cached_files: Optional[List[str]] = None
    CHUNK_SIZE: int = 5000  # Number of lines per chunk

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

    def _run(self, root_package: str, line: int = 0) -> str:
        """
        Run the tool with the given input.
        """
        if root_package.startswith("repos."):
            root_package = root_package.split("repos.")[-1]
        logging.info(f"[Package Tool] Reading packages for {root_package} starting from line {line}")
        try:
            return self.read_file(root_package, line)
        except NoRootPackageFoundError as e:
            logging.error(f"[Package Tool] Error: {e.message}")
            return f"Error: {e.message}"

    def read_file(self, root_package: str, start_line: int = 0) -> str:
        """
        Read the file from the given path with pagination support.
        """
        root_package = root_package.split(".")[0]

        for path in self.cached_files:
            if root_package in path.name:
                logging.info(f"[Package Tool] Found file {path}")
                content = read_dot_file(path)
                result_lines, has_more = self._format_and_paginate_results(content, start_line)
                
                final_result = f"Package relations for {root_package} (starting from line {start_line}):\n"
                final_result += "\n".join(result_lines)
                
                if has_more:
                    next_line = start_line + len(result_lines)
                    final_result += f"\n\n[Output truncated. Use 'line={next_line}' to see the next chunk of results.]"
                
                return final_result

        package_names = [path.name.split("packages_")[-1].split(".dot")[0] for path in self.cached_files]
        raise NoRootPackageFoundError(
            f"Failed to retrieve dependencies for package '{root_package}'. It was not found. Please choose from the available packages: {package_names}")
    
    def _format_and_paginate_results(self, content: Dict, start_line: int) -> Tuple[List[str], bool]:
        """
        Format the package dependencies and paginate the results.
        
        Args:
            root_package: The name of the root package
            content: The dependency content from the dot file
            start_line: The starting line for pagination
            
        Returns:
            Tuple containing:
                - List of formatted result lines
                - Boolean indicating if there are more results
        """
        all_lines = []
        for k, v in content.items():
            all_lines.append(f"package {k} uses {', '.join(v)}")
        
        # Handle pagination
        if start_line >= len(all_lines):
            return ["No more results available."], False
            
        end_line = min(start_line + self.CHUNK_SIZE, len(all_lines))
        result_lines = all_lines[start_line:end_line]
        has_more = end_line < len(all_lines)
        
        return result_lines, has_more
