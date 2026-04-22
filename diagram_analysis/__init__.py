from __future__ import annotations

__all__ = ["DiagramGenerator", "RunContext", "configure_models"]


def __getattr__(name: str):
    if name == "DiagramGenerator":
        from .diagram_generator import DiagramGenerator

        return DiagramGenerator
    if name == "RunContext":
        from .run_context import RunContext

        return RunContext
    if name == "configure_models":
        from agents.llm_config import configure_models

        return configure_models
    raise AttributeError(name)
