import logging
from pathlib import Path
from langchain_core.tools import ArgsSchema
from pydantic import BaseModel, Field
from agents.tools.base import BaseRepoTool

logger = logging.getLogger(__name__)


class ReadFileInput(BaseModel):
    """Input for ReadFileTool."""

    file_path: str = Field(
        ..., description="Path to the file to read, use relative paths from the root of the project. "
    )
    line_number: int = Field(..., description="Line number to focus on")


class ReadFileTool(BaseRepoTool):
    name: str = "readFile"
    description: str = (
        "Reads specific file content around a target line number. "
        "Use only when specific implementation details are needed that CFG cannot provide. "
        "Returns 300 lines centered on the requested line. "
        "Avoid exploratory reading - use only when you know exactly what to examine."
    )
    args_schema: ArgsSchema | None = ReadFileInput
    return_direct: bool = False

    @property
    def cached_files(self) -> list[Path]:
        files = self.context.get_files()
        return sorted(files, key=lambda x: len(x.parts))

    def _run(self, file_path: str, line_number: int) -> str:
        """
        Run the tool with the given input.
        """
        logger.info(f"[ReadFile Tool] Reading file {file_path} around line {line_number}")

        file_path_obj = Path(file_path)
        read_file: Path | None = None
        if self.cached_files:
            for cached_file in self.cached_files:
                if self.is_subsequence(file_path_obj, cached_file):
                    read_file = cached_file
                    break

        common_prefix = str(self.repo_dir) if self.repo_dir else ""
        if read_file is None:
            if self.cached_files and self.repo_dir:
                files_str = "\n".join(
                    [str(f.relative_to(self.repo_dir)) for f in self.cached_files if f.suffix == file_path_obj.suffix]
                )
            else:
                files_str = "No files cached"
            logger.error(f"[ReadFile Tool] File {file_path} not found in cached files.")
            return (
                f"Error: The specified file '{file_path}' was not found in the indexed source files. "
                f"Please ensure the path is correct and points to an existing file: {common_prefix}/\n{files_str}."
            )

        # Read the file content
        with open(read_file, "r", encoding="utf-8") as file:
            lines = file.readlines()

        total_lines = len(lines)
        if line_number < 0 or line_number >= total_lines:
            logger.error(f"[ReadFile Tool] Line number {line_number} is out of range. Total lines: {total_lines}")
            return f"Error: Line number {line_number} is out of range (0-{total_lines - 1})"

        # Calculate start and end line numbers
        if line_number < 150:
            start_line = 0
            end_line = min(total_lines, 300)
        else:
            start_line = max(0, line_number - 150)
            end_line = min(total_lines, start_line + 300)
            if end_line - start_line < 300 and start_line > 0:
                potential_start = max(0, total_lines - 300)
                if potential_start < start_line:
                    start_line = potential_start

        selected_lines = lines[start_line:end_line]
        numbered_lines = [f"{i + 1 + start_line:4}:{line}" for i, line in enumerate(selected_lines)]
        content = "".join(numbered_lines)
        logger.info(f"[ReadFile Tool] Successfully read {len(selected_lines)} lines from {file_path} ")
        return (
            f"File: {file_path}\nLines {start_line}-{end_line - 1} (centered around line {line_number}):\n\n{content}"
        )
