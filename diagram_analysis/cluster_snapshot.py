"""In-memory view of persisted Infomap cluster lineage."""

import logging
from dataclasses import dataclass, field

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import build_all_cluster_results
from static_analyzer.clustering import ClusterResult
from static_analyzer.program_graph import ProgramGraph

logger = logging.getLogger(__name__)


@dataclass
class ClusterSnapshotEntry:
    members: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)
    # Per-qname path used by cluster_delta for diff-scoping; empty on legacy snapshots.
    member_files: dict[str, str] = field(default_factory=dict)


@dataclass
class ClusterSnapshot:
    by_language: dict[str, dict[int, ClusterSnapshotEntry]] = field(default_factory=dict)

    def get_language(self, language: str) -> dict[int, ClusterSnapshotEntry]:
        return self.by_language.get(language, {})

    def all_cluster_ids(self) -> set[int]:
        return {cid for entries in self.by_language.values() for cid in entries.keys()}


def snapshot_from_static_analysis(static_analysis: StaticAnalysisResults) -> ClusterSnapshot:
    """Build a snapshot from persisted ProgramGraph Infomap state."""
    graphs: dict[str, ProgramGraph] = {}
    for language in static_analysis.get_languages():
        graph = static_analysis.get_program_graph(language)
        if graph.cluster_snapshot is not None:
            graphs[str(language)] = graph
    if not graphs:
        return ClusterSnapshot()

    cluster_results = build_all_cluster_results(static_analysis)
    by_language: dict[str, dict[int, ClusterSnapshotEntry]] = {}
    for language, graph in graphs.items():
        result = cluster_results.get(language)
        if result is not None:
            by_language[language] = _entries_from_program_graph(result, graph)
    return ClusterSnapshot(by_language=by_language)


def _entries_from_program_graph(
    cluster_result: ClusterResult,
    graph: ProgramGraph,
) -> dict[int, ClusterSnapshotEntry]:
    """Build entries from the stable cluster result and canonical symbols."""
    entries: dict[int, ClusterSnapshotEntry] = {}
    for cid, members in cluster_result.clusters.items():
        entry = ClusterSnapshotEntry(members=set(members))
        for qname in members:
            node = graph.nodes.get(qname)
            if node is None:
                continue
            file_path = node.file_path
            if file_path:
                entry.files.add(file_path)
                entry.member_files[qname] = file_path
        entries[cid] = entry
    return entries


def snapshot_from_cluster_results(cluster_results: dict[str, ClusterResult]) -> ClusterSnapshot:
    """Build a snapshot directly from cluster results (test helper).

    ``member_files`` is left empty since ``ClusterResult`` has no per-qname
    file map; tests needing diff-scoping must populate it manually.
    """
    by_language: dict[str, dict[int, ClusterSnapshotEntry]] = {}
    for language, result in cluster_results.items():
        by_language[language] = {
            cid: ClusterSnapshotEntry(
                members=set(members),
                files=set(result.cluster_to_files.get(cid, set())),
            )
            for cid, members in result.clusters.items()
        }
    return ClusterSnapshot(by_language=by_language)
