from typing import Any
import logging

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


class MethodInvocationsInput(BaseModel):
    method: str = Field(description="The name of the method for which to retrieve it's immediate callees and calls. ")


class MethodInvocationsTool(BaseTool):
    name: str = "getMethodInvocationsTool"
    description: str = (
        "Retrieves complete project control flow graph (CFG) in DOT format. "
        "**FOUNDATIONAL TOOL** - Use once at the start of analysis to understand overall architecture. "
        "Shows all function/method calls and execution flow across the codebase. "
        "**PRIMARY DATA SOURCE** - Analyze this output before using other tools. "
        "No input arguments required."
    )
    cfg: dict[Any, list[Any]] = None

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

    def _run(self, method) -> str:
        """
        Executes the tool to read and return the project's control flow graph.
        """
        callees = []
        calls = []

        for k, v in self.cfg.items():
            if k == method:
                calls.extend(v)
            if method in v:
                callees.append(k)

        logging.info(f"[Method Invocations Tool] Reading method invocations for {method}")

        # Now let's format the output as a incident response
        graph_str = ""
        if not calls:
            graph_str += f"Method {method} has no calls.\n"
        else:
            graph_str += f"Method {method} is calling the following methods {', '.join(calls)}.\n"
        if not callees:
            graph_str += f"Method {method} has no callees.\n"
        else:
            graph_str += f"Method {method} is called by {', '.join(callees)}.\n"
        if not graph_str:
            graph_str = f"Method {method} either has no calls or callees, or does has bad name. Available methods are: {', '.join(self.cfg.keys())}."
        return f"Method Invocations:\n{graph_str.strip()}"
