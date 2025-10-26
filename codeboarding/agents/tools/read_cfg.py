import logging
from typing import Optional

from langchain_core.tools import BaseTool

from codeboarding.agents.agent_responses import Component
from codeboarding.static_analyzer.analysis_result import StaticAnalysisResults

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
            cfg = self.static_analysis.get_cfg(lang)
            logger.info(
                f"[CFG Tool] Reading control flow graph for {lang}, nodes: {len(cfg.nodes)}, edges: {len(cfg.edges)}")
            if cfg is None:
                logging.warning(f"[CFG Tool] No control flow graph found for {lang}.")
                continue
            result_str += f"Control flow graph for {lang}:\n{cfg.llm_str()}\n"
        if not result_str:
            logging.error("[CFG Tool] No control flow graph data available.")
            return "No control flow graph data available. Ensure static analysis was performed correctly."
        return result_str

    def component_cfg(self, component: Component):
        items = 0
        result = f"Control flow graph for {component.name}:\n"
        skip_nodes = []
        for lang in self.static_analysis.get_languages():
            logger.info(f"[CFG Tool] Filtering CFG for component {component.name} in {lang}")
            cfg = self.static_analysis.get_cfg(lang)
            if cfg is None:
                logging.warning(f"[CFG Tool] No control flow graph found for {lang}.")
                continue
            for _, node in cfg.nodes.items():
                if node.file_path not in component.assigned_files:
                    skip_nodes.append(node)
            result += f"{lang}:\n{cfg.llm_str(skip_nodes=skip_nodes)}\n"
            items += len(cfg.nodes) - len(skip_nodes)

        logger.info(f"[CFG Tool] Filtering CFG for component {component.name}, items found: {items}")
        if items == 0:
            return "No control flow graph data available for this component. Ensure static analysis was performed correctly or the component has valid source code references."
        return result
