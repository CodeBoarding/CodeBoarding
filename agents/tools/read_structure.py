import logging
from typing import Optional, List
from langchain_core.tools import ArgsSchema
from pydantic import BaseModel, Field
from agents.tools.base import BaseRepoTool
from .read_packages import NoRootPackageFoundError

logger = logging.getLogger(__name__)


class ClassQualifiedName(BaseModel):
    class_qualified_name: str = Field(description="The fully qualified name of the class, including its package.")


class CodeStructureTool(BaseRepoTool):
    name: str = "getClassHierarchy"
    description: str = (
        "Retrieves class hierarchy and structure for a specific class. "
        "Use strategically - once per analysis phase when component relationships are unclear from CFG. "
        "Provides internal class organization and inheritance patterns. "
        "Use only when CFG data is insufficient for understanding component boundaries. "
        "Focus on main packages only - avoid utility/helper package analysis."
    )
    args_schema: Optional[ArgsSchema] = ClassQualifiedName
    return_direct: bool = False

    def _run(self, qualified_class_name: str) -> str:
        """
        Run the tool with the given input.
        """
        if self.static_analysis is None:
            logger.error("[CodeStructureTool] static_analysis is not set")
            return "Error: Static analysis is not set."

        languages = self.static_analysis.get_languages()
        for lang in languages:
            try:
                # Attempt to retrieve the class hierarchy for the specified qualified class name
                content = self.static_analysis.get_hierarchy(lang)
                if qualified_class_name not in content:
                    continue
                return (
                    f"Class {qualified_class_name} has superclasses: "
                    f"{content[qualified_class_name]['superclasses']} and subclasses: "
                    f"{content[qualified_class_name]['subclasses']}\n"
                )
            except NoRootPackageFoundError as e:
                logger.error(f"Error retrieving class hierarchy: {e.message}")
                continue
        return f"No class hierarchy found for {qualified_class_name}. Double check if the qualified name is correct with the getSourceCode tool."
