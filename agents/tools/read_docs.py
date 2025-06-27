import logging
from pathlib import Path
from typing import Optional

from langchain_core.tools import ArgsSchema, BaseTool
from pydantic import BaseModel, Field


class ReadDocsFile(BaseModel):
    """Input for ReadDocsTool."""
    file_path: Optional[str] = Field(None,
                                     description="Path to the MarkDown file to read, use relative paths from the root of the project. If not provided, will read README.md")


class ReadDocsTool(BaseTool):
    name: str = "readDocs"
    description: str = (
        "Reads documentation files from the repository. "
        "If no file_path is provided, reads the README.md. "
        "Always returns the full content of the file and lists all other available markdown files. "
        "Works with Markdown (.md) files."
    )
    args_schema: Optional[ArgsSchema] = ReadDocsFile
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
        Walk the directory and collect all markdown files.
        """
        for path in root_project_dir.rglob('*.md'):
            self.cached_files.append(path)
        self.cached_files.sort(key=lambda x: len(x.parts))

    def _run(self, file_path: Optional[str] = None) -> str:
        """
        Run the tool with the given input.
        """

        # If no file_path provided, default to README.md
        if file_path is None:
            file_path = "README.md"

        logging.info(f"[ReadDocs Tool] Reading file {file_path}")

        file_path = Path(file_path)

        read_file = None
        for cached_file in self.cached_files:
            if self.is_subsequence(file_path, cached_file):
                read_file = cached_file
                break

        if read_file is None:
            # If README.md not found and it was the default, list available files
            if file_path.name.lower() == "readme.md":
                available_files = [str(f.relative_to(self.repo_dir)) for f in self.cached_files]
                if not available_files:
                    return "No markdown documentation files found in this repository."
                return f"README.md not found. Available markdown documentation files:\n\n" + "\n".join(
                    f"- {f}" for f in available_files)

            files_str = '\n'.join([str(f.relative_to(self.repo_dir)) for f in self.cached_files])
            return f"Error: The specified file '{file_path}' was not found. " \
                   f"Available markdown files:\n{files_str}"

        # Read the file content
        try:
            with open(read_file, 'r', encoding='utf-8') as file:
                content = file.read()
        except Exception as e:
            return f"Error reading file {file_path}: {str(e)}"

        # Always append list of other markdown files
        other_files = [f for f in self.cached_files if f != read_file]
        result = f"File: {file_path}\n\n{content}"

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
        # Calculate start and end line numbers for 200 lines centered around line_number
        if line_number < 100:
            start_line = 0
            end_line = min(total_lines, 200)
        else:
            start_line = max(0, line_number - 100)
            end_line = min(total_lines, start_line + 200)

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
