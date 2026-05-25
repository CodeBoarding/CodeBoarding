"""In-memory cluster snapshot of the prior clustering, used by ``cluster_delta``.

The partition is sourced exclusively from each per-language CFG's
``CallGraph._cluster_cache``, populated by the previous run's
``DiagramGenerator._persist_pkl_with_cluster_cache`` and round-tripped
through the SHA-tagged pkl. When the cache is absent (legacy pkl, first run
on a fresh repo) ``snapshot_from_static_analysis`` returns an empty snapshot;
``DiagramGenerator.generate_analysis_incremental`` then falls back to a
full run, which warms the pkl for every subsequent incremental.
"""

import logging
from dataclasses import dataclass, field

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import ClusterResult

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
    """Reconstruct a ``ClusterSnapshot`` from each per-language CFG's ``_cluster_cache``.

    Languages whose CFG carries no ``_cluster_cache`` (legacy pkl or first-ever
    run on a fresh repo) contribute nothing; the resulting snapshot's
    ``all_cluster_ids()`` will be empty for those languages, which causes
    ``DiagramGenerator.generate_analysis_incremental`` to fall back to a full
    run. After that full run the pkl is re-saved with a populated cache and
    every subsequent incremental rides the warm path.
    """
    by_language: dict[str, dict[int, ClusterSnapshotEntry]] = {}
    for language in static_analysis.get_languages():
        try:
            cfg = static_analysis.get_cfg(language)
        except ValueError:
            continue
        if cfg._cluster_cache is None:
            continue
        by_language[language] = _entries_from_cfg_cache(cfg._cluster_cache, cfg.to_networkx())
    return ClusterSnapshot(by_language=by_language)


def _entries_from_cfg_cache(
    cluster_cache: ClusterResult,
    nx_graph,
) -> dict[int, ClusterSnapshotEntry]:
    """Build ``{cluster_id -> ClusterSnapshotEntry}`` from a CFG's ``_cluster_cache``.

    File paths come straight off the CFG node attributes — authoritative for
    every qname Leiden actually placed into a cluster.
    """
    entries: dict[int, ClusterSnapshotEntry] = {}
    for cid, members in cluster_cache.clusters.items():
        entry = ClusterSnapshotEntry(members=set(members))
        for qname in members:
            attrs = nx_graph.nodes.get(qname)
            if attrs is None:
                continue
            file_path = attrs.get("file_path")
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
