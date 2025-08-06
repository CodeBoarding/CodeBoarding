import logging
from typing import Optional

from langchain_core.tools import BaseTool

from agents.agent_responses import Component
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


class GetCFGTool(BaseTool):
    name: str = "getControlFlowGraph"
    description: str = (
        "Retrieves complete project control flow graph (CFG) showing all method calls. "
        "Primary analysis tool - use this first to understand project execution flow. "
        "Provides graphical representation of function/method relationships. "
        "Essential data - analyze this output thoroughly before using other tools. "
        "No input arguments required."
    )
    static_analysis: Optional[StaticAnalysisResults] = None

    def __init__(self, static_analysis: StaticAnalysisResults):
        super().__init__()
        self.static_analysis = static_analysis

    def _run(self) -> str:
        """
        Executes the tool to read and return the project's control flow graph.
        """
        result_str = ""
        for lang in self.static_analysis.get_languages():
            logger.info(f"[CFG Tool] Reading control flow graph for {lang}")
            cfg = self.static_analysis.get_cfg(lang)
            if cfg is None:
                logging.warning(f"[CFG Tool] No control flow graph found for {lang}.")
                continue
            result_str += f"Control flow graph for {lang}:\n{str(cfg)}\n"
        if not result_str:
            logging.error("[CFG Tool] No control flow graph data available.")
            return "No control flow graph data available. Ensure static analysis was performed correctly."
        return result_str

    def component_cfg(self, component: Component):
        items = 0
        result = f"Control flow graph for {component.name}:\n"
        for lang in self.static_analysis.get_languages():
            logger.info(f"[CFG Tool] Filtering CFG for component {component.name} in {lang}")
            cfg = self.static_analysis.get_cfg(lang)
            if cfg is None:
                logging.warning(f"[CFG Tool] No control flow graph found for {lang}.")
                continue
            for _, node in cfg.nodes.items():
                for ref in component.referenced_source_code:
                    qual_name = ref.qualified_name
                    if "/" in qual_name:
                        qual_name = qual_name.replace("/", ".")
                    if qual_name.endswith(".py"):
                        qual_name = qual_name[:-3]
                    if qual_name == node.fully_qualified_name or qual_name in node.fully_qualified_name or node.fully_qualified_name in qual_name:
                        result += f"Method {node.fully_qualified_name} is calling the following methods: {', '.join(node.methods_called_by_me)}\n"
                        items += 1
                        break

        logger.info(f"[CFG Tool] Filtering CFG for component {component.name}, items found: {items}")
        return result
