import logging

from langchain_core.tools import ArgsSchema

from agents.tools.base import BaseRepoTool

logger = logging.getLogger(__name__)


class ListGitChangesTool(BaseRepoTool):
    name: str = "listGitChanges"
    description: str = "Lists files changed in the incremental run's change set."
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
