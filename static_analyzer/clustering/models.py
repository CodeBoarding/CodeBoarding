"""Data models for the clustering stage."""

import logging
from collections import defaultdict
from dataclasses import dataclass

from agents.agent_responses import ClusterAnalysis
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import CallGraph
from static_analyzer.graph import ClusterResult as ClusterResult

logger = logging.getLogger(__name__)


def combine_cluster_results(cluster_results: dict[str, ClusterResult]) -> ClusterResult:
    """Union per-language ClusterResults into one.

    Cluster IDs are globally unique across languages, so a plain union is safe and
    lets us group every language's leaf clusters against a single meta-graph.
    """
    clusters: dict[int, set[str]] = {}
    cluster_to_files: dict[int, set[str]] = {}
    file_to_clusters: dict[str, set[int]] = defaultdict(set)
    for cr in cluster_results.values():
        clusters.update(cr.clusters)
        cluster_to_files.update(cr.cluster_to_files)
        for file_path, cids in cr.file_to_clusters.items():
            file_to_clusters[file_path].update(cids)
    return ClusterResult(
        clusters=clusters,
        cluster_to_files=cluster_to_files,
        file_to_clusters=dict(file_to_clusters),
        strategy="combined",
    )


@dataclass
class ClusteringResults:
    """One scope's clustering output — the agents' single analysis input.

    Produced for the whole repository (``cluster_project``) or for one
    component's subgraph (``cluster_component``). Carries the
    ``StaticAnalysisResults`` the clustering was derived from, so consumers
    need no separate static-analysis handle.
    """

    #: language -> leaf clusters
    cluster_results: dict[str, ClusterResult]
    #: language -> the call graph the clusters were derived from
    cfg_graphs: dict[str, CallGraph]
    #: deterministic component groups ("Group i"); the LLM only names them
    cluster_analysis: ClusterAnalysis
    #: the static analysis this clustering was derived from
    static_analysis: StaticAnalysisResults
    #: component id whose subgraph this scope is; "" for the whole project
    scope_id: str = ""

    def combined(self) -> ClusterResult:
        """All languages' leaf clusters unioned into one ClusterResult."""
        return combine_cluster_results(self.cluster_results)
