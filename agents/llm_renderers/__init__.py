"""LLM-facing serializers for types that live below the agents layer.

Kept here rather than as methods on the types themselves so the dependency
direction stays ``static_analyzer -> clustering -> agents``: the renderer knows
about the graph, the graph knows nothing about prompts.
"""

from agents.llm_renderers.call_graph import render_call_graph
from agents.llm_renderers.clustering import cluster_group_descriptions, cluster_group_ids, render_cluster_groups

__all__ = ["cluster_group_descriptions", "cluster_group_ids", "render_call_graph", "render_cluster_groups"]
