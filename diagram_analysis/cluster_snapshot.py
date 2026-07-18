"""In-memory cluster snapshot of the prior clustering, used by ``cluster_delta``.

The partition comes from each per-language ``ProgramGraph.cluster_snapshot``,
written by the previous run and round-tripped through the SHA-tagged pkl. When it
is absent (legacy pkl, first run on a fresh repo) ``snapshot_from_static_analysis``
returns an empty snapshot and ``DiagramGenerator.generate_analysis_incremental``
raises rather than silently rebuilding.
"""

import logging
from dataclasses import dataclass, field

from static_analyzer.analysis_result import StaticAnalysisResults
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
    # Languages with clusterable code whose graph carries no cluster snapshot. A
    # non-empty set means the baseline is only partial and incremental cannot proceed.
    missing_snapshot_languages: set[str] = field(default_factory=set)

    def get_language(self, language: str) -> dict[int, ClusterSnapshotEntry]:
        return self.by_language.get(language, {})

    def all_cluster_ids(self) -> set[int]:
        return {cid for entries in self.by_language.values() for cid in entries.keys()}


def snapshot_from_static_analysis(static_analysis: StaticAnalysisResults) -> ClusterSnapshot:
    """Reconstruct a ``ClusterSnapshot`` from each per-language ``ProgramGraph.cluster_snapshot``.

    A language whose graph has symbols but no snapshot is recorded in
    ``missing_snapshot_languages`` rather than silently dropped: reclustering it fresh
    would lose the stable ids the other languages still carry, so the caller must treat
    such a partial baseline as unavailable and raise.
    """
    by_language: dict[str, dict[int, ClusterSnapshotEntry]] = {}
    missing: set[str] = set()
    for language in static_analysis.get_languages():
        try:
            program_graph = static_analysis.get_program_graph(language)
        except ValueError:
            continue
        if program_graph.cluster_snapshot is None:
            if program_graph.symbols:
                missing.add(str(language))
            continue
        by_language[str(language)] = _entries_from_snapshot(
            program_graph.cluster_snapshot.cluster_result, program_graph
        )
    return ClusterSnapshot(by_language=by_language, missing_snapshot_languages=missing)


def _entries_from_snapshot(
    cluster_result: ClusterResult,
    program_graph: ProgramGraph,
) -> dict[int, ClusterSnapshotEntry]:
    """Build ``{cluster_id -> ClusterSnapshotEntry}``, reading file paths off the graph nodes."""
    entries: dict[int, ClusterSnapshotEntry] = {}
    for cid, members in cluster_result.clusters.items():
        entry = ClusterSnapshotEntry(members=set(members))
        for qname in members:
            node = program_graph.nodes.get(qname)
            if node is None or not node.file_path:
                continue
            entry.files.add(node.file_path)
            entry.member_files[qname] = node.file_path
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
