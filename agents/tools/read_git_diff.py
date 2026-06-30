import logging
import subprocess

from langchain_core.tools import ArgsSchema
from pydantic import BaseModel, Field

from agents.tools.base import BaseRepoTool
from repo_utils.path_utils import to_relative_path

logger = logging.getLogger(__name__)


class ReadGitDiffInput(BaseModel):
    file_path: str = Field(..., description="Repo-relative path whose git diff should be read.")
    context_lines: int = Field(default=5, ge=0, le=50, description="Number of context lines around each hunk.")
    max_chars: int = Field(default=20000, ge=1000, le=50000, description="Maximum characters to return.")


class ReadGitDiffTool(BaseRepoTool):
    name: str = "readGitDiff"
    description: str = (
        "Reads the git diff for one changed file from the incremental run's base/target refs. "
        "Use when source structure is not enough to judge whether a cluster change is semantic."
    )
    args_schema: ArgsSchema | None = ReadGitDiffInput
    return_direct: bool = False

    def _run(self, file_path: str, context_lines: int = 5, max_chars: int = 20000) -> str:
        if self.context.changes is None:
            return "Error: No incremental change set is configured for this run."
        normalized_path = to_relative_path(file_path, self.repo_dir)
        allowed_paths = self._changed_paths()
        if allowed_paths and normalized_path not in allowed_paths:
            return f"Error: '{normalized_path}' is not in the incremental change set."

        cmd = [
            "git",
            "-c",
            "core.quotepath=false",
            "diff",
            f"-U{context_lines}",
            "-M",
            "-C",
            "--find-renames=50%",
            self.context.changes.base_ref,
        ]
        if self.context.changes.target_ref:
            cmd.append(self.context.changes.target_ref)
        cmd.extend(["--", normalized_path])

        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            return f"Error reading git diff for '{normalized_path}': {exc.stderr.strip() or exc.stdout.strip()}"

        output = result.stdout.strip()
        if not output:
            return f"No diff found for '{normalized_path}'."
        if len(output) <= max_chars:
            return output
        return output[:max_chars] + f"\n\n[Diff truncated to {max_chars} characters.]"

    def _changed_paths(self) -> set[str]:
        if self.context.changes is None:
            return set()
        paths: set[str] = set()
        for file_change in self.context.changes.files:
            paths.add(file_change.file_path)
            if file_change.old_path:
                paths.add(file_change.old_path)
        return paths


class ListGitChangesTool(BaseRepoTool):
    name: str = "listGitChanges"
    description: str = "Lists files changed in the incremental run's git diff."
    args_schema: ArgsSchema | None = None
    return_direct: bool = False

    def _run(self) -> str:
        if self.context.changes is None:
            return "No incremental change set is configured for this run."
        if not self.context.changes.files:
            return "No changed files."
        lines = []
        for file_change in self.context.changes.files:
            if file_change.old_path:
                lines.append(f"{file_change.status_code}\t{file_change.old_path} -> {file_change.file_path}")
            else:
                lines.append(f"{file_change.status_code}\t{file_change.file_path}")
        return "\n".join(lines)
