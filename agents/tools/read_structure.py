import logging
import os
from pathlib import Path
from typing import Optional, List, Dict, Tuple

from langchain_core.tools import ArgsSchema, BaseTool

from .read_packages import PackageInput, NoRootPackageFoundError
from .utils import read_dot_file


class CodeStructureTool(BaseTool):
    name: str = "getClassHierarchy"
    description: str = (
        "Retrieves class hierarchy and structure for a specific root package. "
        "**STRATEGIC USE ONLY** - Use once per analysis phase when component relationships are unclear from CFG. "
        "Provides internal class organization and inheritance patterns. "
        "**LIMIT**: Use only when CFG data is insufficient for understanding component boundaries. "
        "Focus on main packages only - avoid utility/helper package analysis."
    )
    args_schema: Optional[ArgsSchema] = PackageInput
    return_direct: bool = False
    cached_files: Optional[List[str]] = None
    CHUNK_SIZE: int = 5000  # Number of lines per chunk, matching PackageRelationsTool

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

    def _run(self, root_package: str, line: int = 0) -> str:
        """
        Run the tool with the given input.
        """
        if root_package.startswith("repos."):
            root_package = root_package.split("repos.")[-1]
        logging.info(f"[Structure Tool] Reading structure for {root_package} starting from line {line}")
        try:
            return self.read_file(root_package, line)
        except NoRootPackageFoundError as e:
            return f"Error: {e.message}"

    def read_file(self, root_package: str, start_line: int = 0) -> str:
        """
        Read the file from the given path with pagination support.
        """
        root_package = root_package.split(".")[0]

        for path in self.cached_files:
            if root_package in path.name:
                logging.info(f"[Structure Tool] Found file {path}")
                content = read_dot_file(path)
                result_lines, has_more = self._format_and_paginate_results(content, start_line)
                
                final_result = f"Package structure for {root_package} (starting from line {start_line}):\n"
                final_result += "\n".join(result_lines)
                
                if has_more:
                    next_line = start_line + len(result_lines)
                    final_result += f"\n\n[Output truncated. Use 'line={next_line}' to see the next chunk of results.]"
                
                return final_result

        package_names = [path.name.split("_structure.dot")[0] for path in self.cached_files]
        raise NoRootPackageFoundError(
            f"Class structure for package '{root_package}' not found. Available packages with structure information: {package_names}")

    def _format_and_paginate_results(self, content: Dict, start_line: int) -> Tuple[List[str], bool]:
        """
        Format the structure content and paginate the results.
        
        Args:
            content: The structure content from the dot file
            start_line: The starting line for pagination
            
        Returns:
            Tuple containing:
                - List of formatted result lines
                - Boolean indicating if there are more results
        """
        # Convert the content dictionary into a list of lines
        all_lines = []
        for k, v in content.items():
            if isinstance(v, list):
                all_lines.append(f"{k} is related to {', '.join(v)}")
            else:
                all_lines.append(f"{k}: {v}")
        
        # Handle pagination
        if start_line >= len(all_lines):
            return ["No more results available."], False
            
        end_line = min(start_line + self.CHUNK_SIZE, len(all_lines))
        result_lines = all_lines[start_line:end_line]
        has_more = end_line < len(all_lines)
        
        return result_lines, has_more
