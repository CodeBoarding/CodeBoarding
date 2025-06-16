from typing import Any
import logging

from langchain_core.tools import BaseTool

from agents.agent_responses import Component


class GetCFGTool(BaseTool):
    name: str = "getControlFlowGraph"
    description: str = (
        "Retrieves the **complete control flow graph (CFG)** for the entire project. "
        "This tool provides a graphical representation (in DOT format) of "
        "all function and method calls across the codebase, illustrating "
        "the execution flow and inter-function relationships. "
        "It's useful for understanding the overall architecture and call sequences "
        "within the project. This tool requires no input arguments."
    )
    cfg: dict[Any, list[Any]] = None

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

    def _run(self) -> str:
        """
        Executes the tool to read and return the project's control flow graph.
        """
        # No input needed for this tool, as it always reads a predefined file.

        # Now let's format the output as a incident response
        graph_str = ""
        logging.info("[CFG Tool] Reading control flow graph")
        for k, v in self.cfg.items():
            graph_str += f"Method {k} is calling the following methods: {', '.join(v)}.\n"
        return f"Control Flow Graph:\n{graph_str.strip()}"

    def component_cfg(self, component: Component):
        component_cfg = {}
        for k, v in self.cfg.items():
            for ref in component.referenced_source_code:
                if ref.qualified_name == k or ref.qualified_name in k or k in ref.qualified_name:
                    component_cfg[k] = v
                    break

        logging.info(f"[CFG Tool] Filtering CFG for component {component.name}")
        graph_str = ""
        for k, v in component_cfg.items():
            graph_str += f"Method {k} is calling the following methods: {', '.join(v)}.\n"
        return f"Control Flow Graph:\n{graph_str.strip()}"
