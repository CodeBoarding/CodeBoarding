import logging
from typing import Optional

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from static_analyzer.analysis_result import StaticAnalysisResults


logger = logging.getLogger(__name__)


class MethodInvocationsInput(BaseModel):
    method: str = Field(description="The name of the method for which to retrieve it's immediate callees and calls. ")


class MethodInvocationsTool(BaseTool):
    name: str = "getMethodInvocationsTool"
    description: str = (
        "Retrieves complete project control flow graph (CFG) in DOT format. "
        "Use once at the start of analysis to understand overall architecture. "
        "Shows all function/method calls and execution flow across the codebase. "
        "Primary data source - analyze this output before using other tools. "
        "No input arguments required."
    )
    static_analysis: Optional[StaticAnalysisResults] = None

    def __init__(self, static_analysis):
        super().__init__()
        self.static_analysis = static_analysis

    def _run(self, method) -> str:
        """
        Executes the tool to read and return the project's control flow graph.
        """

        results = ""
        for lang in self.static_analysis.get_languages():
            # Attempt to retrieve the control flow graph for the specified language
            cfg = self.static_analysis.get_cfg(lang)
            for edge in cfg.edges:
                if edge.src_node.fully_qualified_name == method:
                    results += (
                        f"Method {edge.src_node.fully_qualified_name} is calling {edge.dst_node.fully_qualified_name}\n"
                    )
                if edge.dst_node.fully_qualified_name == method:
                    results += f"Method {edge.dst_node.fully_qualified_name} is called by {edge.src_node.fully_qualified_name}\n"
        if results:
            return results.strip()
        # If no results found, return a message indicating no calls or callees
        logger.warning(f"[MethodInvocationsTool] No method invocations found for {method}.")
        return f"No method invocations found for the {method}. Try reading the source with the `getSourceCode` tool for full details."
