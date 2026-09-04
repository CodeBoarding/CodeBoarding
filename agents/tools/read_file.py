import logging

from langchain_core.tools import ArgsSchema
from pydantic import BaseModel, Field

from agents.tools.base import BaseRepoTool
from repo_utils.path_utils import normalize_repo_path

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

    def _run(self, file_path: str, line_number: int) -> str:
        """Read an exact repository-relative file path around one line."""
        logger.info(f"[ReadFile Tool] Reading file {file_path} around line {line_number}")

        normalized_path = normalize_repo_path(file_path, self.repo_dir)
        if self.context.scope_restricted and normalized_path not in self.context.scope_files:
            logger.warning("[ReadFile Tool] Rejected out-of-scope file %s", file_path)
            return f"Error: File '{file_path}' is outside the current analysis scope."

        read_file = (self.repo_dir / normalized_path).resolve()
        try:
            read_file.relative_to(self.repo_dir.resolve())
        except ValueError:
            return f"Error: File '{file_path}' is outside the repository."
        if not read_file.is_file():
            logger.error("[ReadFile Tool] File %s does not exist.", file_path)
            return f"Error: The specified file '{file_path}' was not found in the indexed source files."

        with open(read_file, "r", encoding="utf-8") as file:
            lines = file.readlines()

        total_lines = len(lines)
        if line_number < 0 or line_number >= total_lines:
            logger.error(f"[ReadFile Tool] Line number {line_number} is out of range. Total lines: {total_lines}")
            return f"Error: Line number {line_number} is out of range (0-{total_lines - 1})"

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
