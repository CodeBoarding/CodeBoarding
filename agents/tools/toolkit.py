import logging
from typing import cast

from .base import RepoContext, BaseRepoTool
from .read_source import CodeReferenceReader
from .read_structure import CodeStructureTool
from .read_packages import PackageRelationsTool
from .read_file_structure import FileStructureTool
from .read_cfg import GetCFGTool
from .get_method_invocations import MethodInvocationsTool
from .read_file import ReadFileTool
from .read_docs import ReadDocsTool
from .get_external_deps import ExternalDepsTool
from .read_git_diff import ReadDiffTool, FileChange

logger = logging.getLogger(__name__)


class CodeBoardingToolkit:
    """
    A professional-grade toolkit that manages the lifecycle and dependency injection
    for all repository-aware tools.
    """

    def __init__(self, context: RepoContext):
        self.context = context
        self._tools: dict[str, BaseRepoTool] = {}

    @property
    def read_source_reference(self) -> CodeReferenceReader:
        if "read_source_reference" not in self._tools:
            self._tools["read_source_reference"] = CodeReferenceReader(context=self.context)
        return cast(CodeReferenceReader, self._tools["read_source_reference"])

    @property
    def read_packages(self) -> PackageRelationsTool:
        if "read_packages" not in self._tools:
            self._tools["read_packages"] = PackageRelationsTool(context=self.context)
        return cast(PackageRelationsTool, self._tools["read_packages"])

    @property
    def read_structure(self) -> CodeStructureTool:
        if "read_structure" not in self._tools:
            self._tools["read_structure"] = CodeStructureTool(context=self.context)
        return cast(CodeStructureTool, self._tools["read_structure"])

    @property
    def read_file_structure(self) -> FileStructureTool:
        if "read_file_structure" not in self._tools:
            self._tools["read_file_structure"] = FileStructureTool(context=self.context)
        return cast(FileStructureTool, self._tools["read_file_structure"])

    @property
    def read_cfg(self) -> GetCFGTool:
        if "read_cfg" not in self._tools:
            self._tools["read_cfg"] = GetCFGTool(context=self.context)
        return cast(GetCFGTool, self._tools["read_cfg"])

    @property
    def read_method_invocations(self) -> MethodInvocationsTool:
        if "read_method_invocations" not in self._tools:
            self._tools["read_method_invocations"] = MethodInvocationsTool(context=self.context)
        return cast(MethodInvocationsTool, self._tools["read_method_invocations"])

    @property
    def read_file(self) -> ReadFileTool:
        if "read_file" not in self._tools:
            self._tools["read_file"] = ReadFileTool(context=self.context)
        return cast(ReadFileTool, self._tools["read_file"])

    @property
    def read_docs(self) -> ReadDocsTool:
        if "read_docs" not in self._tools:
            self._tools["read_docs"] = ReadDocsTool(context=self.context)
        return cast(ReadDocsTool, self._tools["read_docs"])

    @property
    def external_deps(self) -> ExternalDepsTool:
        if "external_deps" not in self._tools:
            self._tools["external_deps"] = ExternalDepsTool(context=self.context)
        return cast(ExternalDepsTool, self._tools["external_deps"])

    def get_read_diff_tool(self, diffs: list[FileChange]) -> ReadDiffTool:
        """
        Creates a ReadDiffTool instance with the provided diffs.
        Since diffs change per run, we don't cache this tool in the toolkit.
        """
        return ReadDiffTool(context=self.context, diffs=diffs)

    def get_agent_tools(self) -> list[BaseRepoTool]:
        """
        Returns the set of tools traditionally used by the React agent.
        """
        return [
            self.read_source_reference,
            self.read_file,
            self.read_file_structure,
            self.read_structure,
            self.read_packages,
        ]

    def get_all_tools(self) -> list[BaseRepoTool]:
        """
        Returns all tools available in the toolkit.
        """
        return [
            self.read_source_reference,
            self.read_packages,
            self.read_structure,
            self.read_file_structure,
            self.read_cfg,
            self.read_method_invocations,
            self.read_file,
            self.read_docs,
            self.external_deps,
        ]
