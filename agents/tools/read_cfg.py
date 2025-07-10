from typing import Any
import logging

from langchain_core.tools import BaseTool

from agents.agent_responses import Component


class GetCFGTool(BaseTool):
    name: str = "getControlFlowGraph"
    description: str = (
        "Retrieves complete project control flow graph (CFG) showing all method calls. "
        "**PRIMARY ANALYSIS TOOL** - Use this first to understand project execution flow. "
        "Provides graphical representation of function/method relationships. "
        "**ESSENTIAL DATA** - Analyze this output thoroughly before using other tools. "
        "No input arguments required."
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
                qual_name = ref.qualified_name
                if "/" in qual_name:
                    qual_name = qual_name.replace("/", ".")
                if qual_name.endswith(".py"):
                    qual_name = qual_name[:-3]
                if qual_name == k or qual_name in k or k in qual_name:
                    component_cfg[k] = v
                    break

        logging.info(f"[CFG Tool] Filtering CFG for component {component.name}, items found: {len(component_cfg)}")
        graph_str = ""
        for k, v in component_cfg.items():
            graph_str += f"Method {k} is calling the following methods: {', '.join(v)}.\n"
        return f"Control Flow Graph:\n{graph_str.strip()}"
