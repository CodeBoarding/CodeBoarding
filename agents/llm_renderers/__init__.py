"""Renderers that turn static-analysis structures into the text an LLM sees.

Kept in ``agents`` rather than ``static_analyzer``: prompt shape is an agent
concern, and the graph should not know how it gets described.
"""

from agents.llm_renderers.call_graph import render_call_graph

__all__ = ["render_call_graph"]
