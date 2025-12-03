import logging
from pathlib import Path

from langchain_core.tools import ArgsSchema, BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ReadDocsFile(BaseModel):
    """Input for ReadDocsTool."""

    file_path: str | None = Field(
        None,
        description="Path to the documentation file to read, use relative paths from the root of the project. If not provided, will read README.md",
    )
    line_number: int | None = Field(
        0, description="Line number to focus on. The tool will return content centered around this line."
    )


class ReadDocsTool(BaseTool):
    name: str = "readDocs"
    description: str = (
        "Reads project documentation files (README, .md, .rst, .txt). "
        "Use early in analysis to understand project purpose and architecture. "
        "Defaults to README.md if no path specified. "
        "Provides project understanding without code analysis. "
        "Focus on architecture sections, not detailed API documentation."
    )
    args_schema: ArgsSchema | None = ReadDocsFile
    return_direct: bool = False
    cached_files: list[Path] | None = None
    repo_dir: Path | None = None
    LINES_TO_RETURN: int = 300  # Number of lines to return centered around the requested line

    def __init__(self, repo_dir: Path):
        super().__init__()
        self.cached_files = []
        self.repo_dir = repo_dir
        self.walk_dir(repo_dir)

    def walk_dir(self, root_project_dir: Path) -> None:
        """
        Walk the directory and collect all markdown files.
        """
        for pattern in ("*.md", "*.rst", "*.txt", "*.html"):
            for path in root_project_dir.rglob(pattern):
                # Exclude test files and directories
                if "tests" not in path.parts and "test" not in path.name.lower():
                    if self.cached_files is not None:
                        self.cached_files.append(path)
        if self.cached_files:
            self.cached_files.sort(key=lambda x: len(x.parts))

    def _run(self, file_path: str | None = None, line_number: int = 0) -> str:
        """
        Run the tool with the given input.
        """
        # If no file_path provided, default to README.md
        if file_path is None:
            file_path = "README"
        file_path_obj = Path(file_path)

        read_file: Path | None = None
        if self.cached_files:
            for cached_file in self.cached_files:
                if self.is_subsequence(file_path_obj, cached_file):
                    read_file = cached_file
                    break

        if read_file is None:
            # If README.md not found and it was the default, list available files
            if file_path_obj.stem.lower() == "readme":
                if self.cached_files and self.repo_dir:
                    available_files = [str(f.relative_to(self.repo_dir)) for f in self.cached_files]
                    if not available_files:
                        return "No documentation files found in this repository."
                    return "README not found. Available documentation files:\n\n" + "\n".join(
                        f"- {f}" for f in available_files
                    )
                else:
                    return "No documentation files found in this repository."

            if self.cached_files and self.repo_dir:
                files_str = "\n".join([str(f.relative_to(self.repo_dir)) for f in self.cached_files])
            else:
                files_str = "No files available"
            return (
                f"Error: The specified file '{file_path_obj}' was not found. "
                f"Available documentation files:\n{files_str}"
            )

        # Read the file content
        try:
            with open(read_file, "r", encoding="utf-8") as file:
                logger.info(f"[ReadDocs Tool] Reading file {read_file} around line {line_number}")
                lines = file.readlines()
        except Exception as e:
            return f"Error reading file {file_path_obj}: {str(e)}"

        total_lines = len(lines)

        # Validate line number
        if line_number < 0 or line_number >= total_lines:
            if total_lines == 0:
                return f"File {file_path_obj} is empty."
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
        numbered_lines = [f"{i + start_line:4}:{line}" for i, line in enumerate(selected_lines)]
        content = "".join(numbered_lines)

        # Prepare file information header
        file_info = f"File: {file_path_obj}\n"
        if total_lines > self.LINES_TO_RETURN:
            file_info += f"Lines {start_line}-{end_line - 1} (centered around line {line_number}, total lines: {total_lines})\n\n"
        else:
            file_info += f"Full content ({total_lines} lines):\n\n"

        # Always append list of other documentation files
        if self.cached_files:
            other_files = [f for f in self.cached_files if f != read_file]
        else:
            other_files = []
        result = file_info + content

        if other_files and self.repo_dir is not None:
            relative_files = [str(f.relative_to(self.repo_dir)) for f in other_files]
            result += "\n\n--- Other Available Documentation Files ---\n"
            result += "\n".join(f"- {f}" for f in relative_files)

        return result

    def is_subsequence(self, sub: Path, full: Path) -> bool:
        # exclude the analysis_dir from the comparison
        if self.repo_dir is None:
            return False
        sub_parts = sub.parts
        full_parts = full.parts
        repo_dir_parts = self.repo_dir.parts
        full_parts = full_parts[len(repo_dir_parts) :]
        for i in range(len(full_parts) - len(sub_parts) + 1):
            if full_parts[i : i + len(sub_parts)] == sub_parts:
                return True
        return False
