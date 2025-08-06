import logging
from typing import Optional, List

from langchain_core.tools import ArgsSchema, BaseTool
from pydantic import BaseModel, Field

from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


class PackageInput(BaseModel):
    root_package: str = Field(
        description="The top-level package name for which to retrieve dependencies. "
                    "This should be the primary name of the package, without file extensions or full paths. "
                    "For example, use 'langchain' for the `langchain` package, or 'langchain_core' "
                    "for the `langchain_core` package. Do not include 'repos.' prefix if it's present in agent's context."
    )


class NoRootPackageFoundError(Exception):
    """Custom exception for when a root package is not found."""

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class PackageRelationsTool(BaseTool):
    name: str = "getPackageDependencies"
    description: str = (
        "Retrieves package dependencies for a root package. "
        "Use for high-level analysis only - once per analysis to understand main package structure. "
        "Shows hierarchical relationships between modules and sub-packages. "
        "Use only for primary project packages, not for detailed exploration. "
        "Prefer analyzing CFG data before using this tool."
    )
    args_schema: Optional[ArgsSchema] = PackageInput
    return_direct: bool = False
    cached_files: Optional[List[str]] = None
    static_analyzer: Optional[StaticAnalysisResults] = None

    def __init__(self, static_analysis):
        super().__init__()
        self.static_analyzer = static_analysis

    def _run(self, root_package: str, line: int = 0) -> str:
        """
        Run the tool with the given input.
        """
        languages = self.static_analyzer.get_languages()
        packages = []
        for lang in languages:
            try:
                # Attempt to retrieve the package relations for the specified root package
                content = self.static_analyzer.get_package_dependencies(lang)
                if root_package not in content:
                    packages += list(content.keys())
                    continue
                result = content[root_package]
                return f"Package {root_package} imports {result['imports']} and is imported by {result['imported_by']}."
            except ValueError:
                logger.warning(f"[Package Tool] No package relations found for {root_package} in {lang}.")
                continue
        return f"No package relations found for {root_package} in {packages}."
