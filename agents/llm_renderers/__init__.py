"""LLM-facing serializers for types that live below the agents layer.

Kept here rather than as methods on the types themselves so the dependency
direction stays ``static_analyzer -> clustering -> agents``: the renderer knows
about the graph, the graph knows nothing about prompts.
"""

from agents.llm_renderers.call_graph import render_call_graph

__all__ = ["render_call_graph"]
