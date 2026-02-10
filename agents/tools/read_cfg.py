import logging
from agents.agent_responses import Component
from agents.tools.base import BaseRepoTool

logger = logging.getLogger(__name__)


class GetCFGTool(BaseRepoTool):
    name: str = "getControlFlowGraph"
    description: str = (
        "Retrieves complete project control flow graph (CFG) showing all method calls. "
        "Primary analysis tool - use this first to understand project execution flow. "
        "Provides graphical representation of function/method relationships. "
        "Essential data - analyze this output thoroughly before using other tools. "
        "No input arguments required."
    )

    def _run(self) -> str:
        """
        Executes the tool to read and return the project's control flow graph.
        """
        if not self.static_analysis:
            return "No static analysis data available."
        result_str = ""
        for lang in self.static_analysis.get_languages():
            cfg = self.static_analysis.get_cfg(lang)
            logger.info(
                f"[CFG Tool] Reading control flow graph for {lang}, nodes: {len(cfg.nodes)}, edges: {len(cfg.edges)}"
            )
            if cfg is None:
                logging.warning(f"[CFG Tool] No control flow graph found for {lang}.")
                continue
            result_str += f"Control flow graph for {lang}:\n{cfg.llm_str()}\n"
        if not result_str:
            logging.error("[CFG Tool] No control flow graph data available.")
            return "No control flow graph data available. Ensure static analysis was performed correctly."
        return result_str

    def component_cfg(self, component: Component) -> str:
        if not self.static_analysis:
            return "No static analysis data available."
        items = 0
        result = f"Control flow graph for {component.name}:\n"
        skip_nodes: list = []
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
