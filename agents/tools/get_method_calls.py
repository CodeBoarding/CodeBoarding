import logging
from typing import Literal

from langchain_core.tools import ArgsSchema
from pydantic import BaseModel, Field

from agents.tools.base import BaseRepoTool

logger = logging.getLogger(__name__)


class MethodCallsInput(BaseModel):
    qualified_name: str = Field(description="Exact qualified name of the method to inspect.")
    direction: Literal["incoming", "outgoing"] = Field(
        description="Use 'incoming' for callers or 'outgoing' for callees."
    )


class MethodCallsTool(BaseRepoTool):
    name: str = "getMethodCalls"
    description: str = (
        "Returns the immediate callers or callees of one exact method within the current analysis scope. "
        "Use only to resolve a specific uncertainty left by the supplied boundary connections."
    )
    args_schema: ArgsSchema | None = MethodCallsInput

    def _run(self, qualified_name: str, direction: Literal["incoming", "outgoing"]) -> str:
        """Return directed calls for one in-scope method."""
        if not self.static_analysis:
            return "No static analysis data available."
        if self.context.scope_restricted and qualified_name not in self.context.scope_methods:
            return f"Error: Method '{qualified_name}' is outside the current analysis scope."

        if self.context.scope_restricted:
            graphs = self.context.cfg_graphs.values()
        else:
            graphs = (self.static_analysis.get_cfg(language) for language in self.static_analysis.get_languages())

        results: list[str] = []
        for cfg in graphs:
            for edge in cfg.edges:
                source = edge.src_node.fully_qualified_name
                target = edge.dst_node.fully_qualified_name
                if self.context.scope_restricted and (
                    source not in self.context.scope_methods or target not in self.context.scope_methods
                ):
                    continue
                if direction == "outgoing" and source == qualified_name:
                    results.append(f"{source} -> {target}")
                if direction == "incoming" and target == qualified_name:
                    results.append(f"{source} -> {target}")
        if results:
            return "\n".join(sorted(set(results)))
        logger.warning("[MethodCallsTool] No %s calls found for %s.", direction, qualified_name)
        return f"No {direction} calls found for '{qualified_name}' in the current analysis scope."
