import logging
from pathlib import Path
from typing import Optional

from langchain_core.tools import ArgsSchema, BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ReadDocsFile(BaseModel):
    """Input for ReadDocsTool."""
    file_path: Optional[str] = Field(None,
                                     description="Path to the documentation file to read, use relative paths from the root of the project. If not provided, will read README.md")
    line_number: Optional[int] = Field(0,
                                       description="Line number to focus on. The tool will return content centered around this line.")


class ReadDocsTool(BaseTool):
    name: str = "readDocs"
    description: str = (
        "Reads project documentation files (README, .md, .rst, .txt). "
        "Use early in analysis to understand project purpose and architecture. "
        "Defaults to README.md if no path specified. "
        "Provides project understanding without code analysis. "
        "Focus on architecture sections, not detailed API documentation."
    )
    args_schema: Optional[ArgsSchema] = ReadDocsFile
    return_direct: bool = False
    cached_files: Optional[list[Path]] = None
    repo_dir: Optional[Path] = None
    LINES_TO_RETURN: int = 300  # Number of lines to return centered around the requested line

    def __init__(self, repo_dir: Path):
        super().__init__()
        self.cached_files = []
        self.repo_dir = repo_dir
        self.walk_dir(repo_dir)

    def walk_dir(self, root_project_dir):
        """
        Walk the directory and collect all markdown files.
        """
        for pattern in ['*.md', '*.rst', '*.txt', '*.html']:
            for path in root_project_dir.rglob(pattern):
                # Exclude test files and directories
                if "tests" not in path.parts and "test" not in path.name.lower():
                    self.cached_files.append(path)
        self.cached_files.sort(key=lambda x: len(x.parts))

    def _run(self, file_path: Optional[str] = None, line_number: Optional[int] = 0) -> str:
        """
        Run the tool with the given input.
        """
        # If no file_path provided, default to README.md
        if file_path is None:
            file_path = "README"
        file_path = Path(file_path)

        read_file = None
        for cached_file in self.cached_files:
            if self.is_subsequence(file_path, cached_file):
                read_file = cached_file
                break

        if read_file is None:
            # If README.md not found and it was the default, list available files
            if file_path.stem.lower() == "readme":
                available_files = [str(f.relative_to(self.repo_dir)) for f in self.cached_files]
                if not available_files:
                    return "No documentation files found in this repository."
                return f"README not found. Available documentation files:\n\n" + "\n".join(
                    f"- {f}" for f in available_files)

            files_str = '\n'.join([str(f.relative_to(self.repo_dir)) for f in self.cached_files])
            return f"Error: The specified file '{file_path}' was not found. " \
                   f"Available documentation files:\n{files_str}"

        # Read the file content
        try:
            with open(read_file, 'r', encoding='utf-8') as file:
                logger.info(f"[ReadDocs Tool] Reading file {read_file} around line {line_number}")
                lines = file.readlines()
        except Exception as e:
            return f"Error reading file {file_path}: {str(e)}"

        total_lines = len(lines)

        # Validate line number
        if line_number < 0 or line_number >= total_lines:
            if total_lines == 0:
                return f"File {file_path} is empty."
            return f"Error: Line number {line_number} is out of range (0-{total_lines - 1})"

        # Calculate start and end line numbers based on the specified requirements
        if line_number < self.LINES_TO_RETURN // 2:
            start_line = 0
            end_line = min(total_lines, self.LINES_TO_RETURN)
        else:
            # Center lines around the specified line number
            start_line = max(0, line_number - (self.LINES_TO_RETURN // 2))
            end_line = min(total_lines, start_line + self.LINES_TO_RETURN)

            # If we're close to the end of the file and can't get enough lines,
            # adjust the start line to get as many lines as possible
            if end_line - start_line < self.LINES_TO_RETURN and start_line > 0:
                potential_start = max(0, total_lines - self.LINES_TO_RETURN)
                if potential_start < start_line:
                    start_line = potential_start

        # Extract and number the lines
        selected_lines = lines[start_line:end_line]
        numbered_lines = [
            f"{i + start_line:4}:{line}" for i, line in enumerate(selected_lines)
        ]
        content = ''.join(numbered_lines)

        # Prepare file information header
        file_info = f"File: {file_path}\n"
        if total_lines > self.LINES_TO_RETURN:
            file_info += f"Lines {start_line}-{end_line - 1} (centered around line {line_number}, total lines: {total_lines})\n\n"
        else:
            file_info += f"Full content ({total_lines} lines):\n\n"

        # Always append list of other documentation files
        other_files = [f for f in self.cached_files if f != read_file]
        result = file_info + content

        if other_files:
            relative_files = [str(f.relative_to(self.repo_dir)) for f in other_files]
            result += f"\n\n--- Other Available Documentation Files ---\n"
            result += "\n".join(f"- {f}" for f in relative_files)

        return result

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
