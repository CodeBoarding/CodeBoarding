"""Serialize a finished analysis' call graph and every level of its clustering.

The pipeline records a scoped cluster id per method at every level it clustered
(``CallGraph.method_cluster_paths``) and the component tree that owns those
clusters (``analysis.json``). Together they are enough to rebuild the whole
hierarchy offline — no pipeline instrumentation, no re-running the LLM — and to
replay the deterministic grouping decision that produced each level.

``export_clustering`` builds the payload, ``render_html`` inlines it into a
self-contained viewer.
"""

from cluster_viz.export import export_clustering
from cluster_viz.render import render_html

__all__ = ["export_clustering", "render_html"]
