"""Clustering stage of the analysis pipeline.

Sits between static analysis and the LLM agents: ``ClusteringService`` turns
``StaticAnalysisResults`` into ``ClusteringResults`` (leaf clusters, scoped call
graphs, and their deterministic grouping), which the agents receive as plain
inputs. ``clustering.assignment`` holds the deterministic post-LLM pass that
pins the named components back onto those clusters.
"""

from clustering.service import ClusteringResults, ClusteringService, scoped_snapshot_from_lineage

__all__ = ["ClusteringResults", "ClusteringService", "scoped_snapshot_from_lineage"]
