import logging
from typing import List, Optional
from langchain_core.tools import ArgsSchema, BaseTool
from pydantic import BaseModel, Field

from repo_utils.git_diff import FileChange

logger = logging.getLogger(__name__)


class ReadDiffInput(BaseModel):
    """Input for ReadDiffTool."""

    file_path: str = Field(
        ..., description="Path to the file to read diff for, use relative paths from the root of the project"
    )
    line_number: int = Field(
        default=1,
        description="Line number to focus on within the diff (1-based). For large diffs, this allows viewing different sections. Default is 1 to start from the beginning.",
    )


class ReadDiffTool(BaseTool):
    name: str = "readDiffFile"
    description: str = (
        "Reads the diff for a specified file and returns the changes made (additions and deletions). "
        "This tool shows what lines were added (+) and removed (-) in the file. "
        "For large diffs, it shows up to 100 lines around the specified line_number. "
        "If the diff is truncated, you can call this tool again with a different line_number to see other sections."
    )
    args_schema: Optional[ArgsSchema] = ReadDiffInput
    return_direct: bool = False
    diffs: List[FileChange] = None

    def __init__(self, diffs: List[FileChange]):
        super().__init__()
        self.diffs = diffs

    def _run(self, file_path: str, line_number: int = 1) -> str:
        """
        Run the tool with the given input.
        """
        logger.info(f"[ReadDiff Tool] Reading diff for file {file_path} around line {line_number}")

        # Find the matching file change
        matching_change = None
        for change in self.diffs:
            if change.filename == file_path or change.filename.endswith(file_path):
                matching_change = change
                break

        if matching_change is None:
            # Provide helpful error message with available files
            available_files = [change.filename for change in self.diffs]
            files_str = "\n".join(available_files) if available_files else "No files with changes found"
            return f"Error: No diff found for file '{file_path}'. Available files with changes:\n{files_str}"

        # Format the diff output
        result = [
            f"File: {matching_change.filename}",
            f"Total additions: {matching_change.additions}, Total deletions: {matching_change.deletions}",
            "",
        ]

        # Combine all diff lines for pagination
        all_diff_lines = []

        # Add deletions with prefixes
        for line in matching_change.removed_lines:
            all_diff_lines.append(f"- {line}")

        # Add additions with prefixes
        for line in matching_change.added_lines:
            all_diff_lines.append(f"+ {line}")

        total_diff_lines = len(all_diff_lines)

        if total_diff_lines == 0:
            result.append(
                "No detailed line changes available (file may have been moved, renamed, or had binary changes)"
            )
            return "\n".join(result)

        # Handle pagination similar to ReadFileTool
        max_lines_to_show = 100
        line_number = max(1, line_number)  # Ensure line_number is at least 1

        if line_number > total_diff_lines:
            result.append(f"Error: Line number {line_number} is out of range (1-{total_diff_lines})")
            return "\n".join(result)

        # Calculate start and end line numbers
        if line_number <= 50:
            start_line = 0
            end_line = min(total_diff_lines, max_lines_to_show)
        else:
            # Center around the specified line number
            start_line = max(0, line_number - 51)  # -51 because line_number is 1-based
            end_line = min(total_diff_lines, start_line + max_lines_to_show)

            # If we're close to the end and can't get max_lines_to_show lines,
            # adjust the start line to get as many lines as possible
            if end_line - start_line < max_lines_to_show and start_line > 0:
                potential_start = max(0, total_diff_lines - max_lines_to_show)
                if potential_start < start_line:
                    start_line = potential_start

        # Extract the lines to display
        displayed_lines = all_diff_lines[start_line:end_line]

        result.append(f"=== DIFF CONTENT (Lines {start_line + 1}-{end_line} of {total_diff_lines}) ===")
        for i, line in enumerate(displayed_lines):
            result.append(f"{start_line + i + 1:4}: {line}")

        # Add truncation notice if needed
        if total_diff_lines > max_lines_to_show:
            if end_line < total_diff_lines:
                result.append("")
                result.append(
                    f"*** DIFF TRUNCATED: Showing lines {start_line + 1}-{end_line} of {total_diff_lines} total diff lines ***"
                )
                result.append(f"To see more, call this tool again with line_number > {end_line}")
            elif start_line > 0:
                result.append("")
                result.append(
                    f"*** DIFF TRUNCATED: Showing lines {start_line + 1}-{end_line} of {total_diff_lines} total diff lines ***"
                )
                result.append(f"To see earlier content, call this tool again with a smaller line_number")

        return "\n".join(result)
