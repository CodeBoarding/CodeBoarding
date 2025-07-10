import logging
from pathlib import Path
from typing import Optional

from langchain_core.tools import ArgsSchema, BaseTool
from pydantic import BaseModel, Field


class ReadFileInput(BaseModel):
    """Input for ReadFileTool."""
    file_path: str = Field(...,
                           description="Path to the Python file to read, use relative paths from the root of the project. ")
    line_number: int = Field(..., description="Line number to focus on")


class ReadFileTool(BaseTool):
    name: str = "readFile"
    description: str = (
        "Reads a specified Python file, returning 200 lines of content centered around the requested line number. "
        "This tool is useful for examining source code in detail, by focusing on a specific section of a file. "
        "It only works with Python (.py) files."
    )
    args_schema: Optional[ArgsSchema] = ReadFileInput
    return_direct: bool = False
    cached_files: Optional[list[Path]] = None
    repo_dir: Optional[Path] = None

    def __init__(self, repo_dir: Path):
        super().__init__()
        self.cached_files = []
        self.repo_dir = repo_dir
        self.walk_dir(repo_dir)

    def walk_dir(self, root_project_dir):
        """
        Walk the directory and collect all files.
        """
        for path in root_project_dir.rglob('*.py'):
            self.cached_files.append(path)
        self.cached_files.sort(key=lambda x: len(x.parts))

    def _run(self, file_path: str, line_number: int) -> str:
        """
        Run the tool with the given input.
        """

        logging.info(f"[ReadFile Tool] Reading file {file_path} around line {line_number}")

        file_path = Path(file_path)

        read_file = None
        for cached_file in self.cached_files:
            if self.is_subsequence(file_path, cached_file):
                read_file = cached_file
                break

        common_prefix = str(self.repo_dir)
        if len(file_path.suffixes) != 1 or file_path.suffix not in ['.py', '.md', '.txt', '.rst', '.yml']:
            return f"Error: The specified file '{file_path}' is not a supported file type. " \
                   f"Supported types are: .py, .md, .txt, .rst, .yml.\n"
        if read_file is None:
            files_str = '\n'.join(
                [str(f.relative_to(self.repo_dir)) for f in self.cached_files if f.suffix == file_path.suffix])
            return f"Error: The specified file '{file_path}' was not found in the indexed source files. " \
                   f"Please ensure the path is correct and points to an existing Python file: {common_prefix}/\n{files_str}."

        # Read the file content
        with open(read_file, 'r', encoding='utf-8') as file:
            lines = file.readlines()

        total_lines = len(lines)

        if line_number < 0 or line_number >= total_lines:
            return f"Error: Line number {line_number} is out of range (0-{total_lines - 1})"

        # Calculate start and end line numbers based on the specified requirements
        if line_number < 100:
            start_line = 0
            end_line = min(total_lines, 200)
        else:
            # Center 200 lines around the specified line number
            start_line = max(0, line_number - 100)
            end_line = min(total_lines, start_line + 200)

            # If we're close to the end of the file and can't get 200 lines,
            # adjust the start line to get as many lines as possible (up to 200)
            if end_line - start_line < 200 and start_line > 0:
                potential_start = max(0, total_lines - 200)
                if potential_start < start_line:
                    start_line = potential_start

        # Extract and number the lines
        selected_lines = lines[start_line:end_line]
        numbered_lines = [
            f"{i + 1 + start_line:4}:{line}" for i, line in enumerate(selected_lines)
        ]
        content = ''.join(numbered_lines)

        return f"File: {file_path}\nLines {start_line}-{end_line - 1} (centered around line {line_number}):\n\n{content}"

    def is_subsequence(self, sub: Path, full: Path) -> bool:
        # exclude the analysis_dir from the comparison
        sub = sub.parts
        full = full.parts
        repo_dir = self.repo_dir.parts
        full = full[len(repo_dir):]
        for i in range(len(full) - len(sub) + 1):
            if full[i:i + len(sub)] == sub:
                return True
        return False
