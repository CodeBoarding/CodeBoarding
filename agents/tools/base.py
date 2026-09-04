from pathlib import Path

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph


class RepoContext(BaseModel):
    """Dependencies and scope restrictions shared by the semantic tools."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    repo_dir: Path
    static_analysis: StaticAnalysisResults | None = None
    cfg_graphs: dict[str, CallGraph] = Field(default_factory=dict)
    scope_restricted: bool = False
    scope_files: frozenset[str] = frozenset()
    scope_methods: frozenset[str] = frozenset()


class BaseRepoTool(BaseTool):
    """Base class for repository tools with a shared bounded context."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    context: RepoContext = Field(description="The repository context containing shared dependencies.")

    @property
    def repo_dir(self) -> Path:
        return self.context.repo_dir

    @property
    def static_analysis(self) -> StaticAnalysisResults | None:
        return self.context.static_analysis
