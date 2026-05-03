"""In-memory cluster snapshot reconstructed from ``analysis.json``.

The incremental pipeline needs the prior CFG clustering (per-cluster
member sets) to compute a delta against fresh static analysis. Earlier
revisions of this module wrote a sidecar ``cluster_snapshot.json`` next
to ``analysis.json`` to carry that state. We now persist the same
information inline on each ``Component`` (``cluster_members``) so a
single file is the source of truth for both the user-facing analysis
and the incremental baseline.

This module is the reader: it walks the loaded ``AnalysisInsights``
tree, collects per-component ``cluster_members``, and partitions the
result by programming language using the CFG-derived file paths from
``StaticAnalysisResults``.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from agents.agent_responses import AnalysisInsights
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import ClusterResult

logger = logging.getLogger(__name__)


@dataclass
class ClusterSnapshotEntry:
    members: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)
    # Per-qname file path, used by ``cluster_delta`` to decide whether a
    # removed qname's file is in the source diff. Only populated when the
    # owning component had a ``file_methods`` entry covering that qname; on
    # legacy snapshots it can stay empty without breaking anything.
    member_files: dict[str, str] = field(default_factory=dict)


@dataclass
class ClusterSnapshot:
    by_language: dict[str, dict[int, ClusterSnapshotEntry]] = field(default_factory=dict)

    def get_language(self, language: str) -> dict[int, ClusterSnapshotEntry]:
        return self.by_language.get(language, {})

    def all_cluster_ids(self) -> set[int]:
        return {cid for entries in self.by_language.values() for cid in entries.keys()}


def snapshot_from_analysis(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    static_analysis: StaticAnalysisResults,
) -> ClusterSnapshot:
    """Reconstruct a ``ClusterSnapshot`` from the loaded analysis tree.

    Walks every component (root + sub-analyses), collects each member from
    ``Component.cluster_members``, and partitions the result by language.

    Each qname is bucketed by language using two signals, in order:
      1. The fresh CFG (if it still contains the qname).
      2. The qname's prior ``file_methods`` file_path: extension -> language
         via the fresh CFG's known extensions.

    The second signal lets us keep deleted/drifted qnames in the snapshot
    so ``compute_cluster_delta`` can correctly compute ``removed_nodes``
    instead of silently filtering them out — the diff-scope filter in
    ``cluster_delta._delta_for_language`` is the right place to ignore
    drift, not this reconstruction.
    """
    qname_to_language = _build_qname_language_index(static_analysis)
    qname_to_file_fresh: dict[str, str] = {}
    ext_to_language: dict[str, str] = {}
    for language in static_analysis.get_languages():
        try:
            cfg = static_analysis.get_cfg(language)
        except ValueError:
            continue
        for node, attrs in cfg.to_networkx().nodes(data=True):
            file_path = attrs.get("file_path")
            if not file_path:
                continue
            qname_to_file_fresh.setdefault(node, file_path)
            ext = PurePosixPath(file_path.replace("\\", "/")).suffix.lower()
            if ext:
                ext_to_language.setdefault(ext, language)

    # Walk every component once to build the prior qname-to-file map from the
    # already-persisted ``file_methods`` groups. Components reuse the same
    # mapping for every cluster they own, so we cache by ``id`` to skip
    # duplicate components that surface from both the root and sub-analyses.
    qname_to_file_old: dict[str, str] = {}
    seen_components_for_files: set[int] = set()
    for component in _iter_components(root_analysis, sub_analyses):
        if id(component) in seen_components_for_files:
            continue
        seen_components_for_files.add(id(component))
        for group in component.file_methods:
            for method in group.methods:
                qname_to_file_old.setdefault(method.qualified_name, group.file_path)

    by_language: dict[str, dict[int, ClusterSnapshotEntry]] = defaultdict(dict)
    seen_components: set[int] = set()
    unbucketed_qnames = 0
    for component in _iter_components(root_analysis, sub_analyses):
        if id(component) in seen_components:
            continue
        seen_components.add(id(component))
        for cid, qnames in component.cluster_members.items():
            for qname in qnames:
                file_path = qname_to_file_fresh.get(qname) or qname_to_file_old.get(qname)
                language = qname_to_language.get(qname)
                if language is None and file_path is not None:
                    ext = PurePosixPath(file_path.replace("\\", "/")).suffix.lower()
                    language = ext_to_language.get(ext)
                if language is None:
                    # Truly unattributable: no fresh CFG entry, no prior
                    # file_methods entry to derive a language from. Drop
                    # it so the delta isn't poisoned by phantom qnames.
                    unbucketed_qnames += 1
                    continue
                lang_entries = by_language[language]
                entry = lang_entries.get(cid)
                if entry is None:
                    entry = ClusterSnapshotEntry()
                    lang_entries[cid] = entry
                entry.members.add(qname)
                if file_path:
                    entry.files.add(file_path)
                    entry.member_files[qname] = file_path

    if unbucketed_qnames:
        logger.info(
            "snapshot_from_analysis: skipped %d cluster members with no resolvable language",
            unbucketed_qnames,
        )
    return ClusterSnapshot(by_language=dict(by_language))


def snapshot_from_cluster_results(cluster_results: dict[str, ClusterResult]) -> ClusterSnapshot:
    """Build an in-memory snapshot directly from cluster results (used by tests).

    Note: ``ClusterResult`` doesn't carry a per-qname file map, so
    ``ClusterSnapshotEntry.member_files`` is left empty. Tests that need the
    diff-scoping behavior should populate it manually after construction.
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


def _build_qname_language_index(static_analysis: StaticAnalysisResults) -> dict[str, str]:
    """Map every CFG node's qualified name to the language whose CFG owns it."""
    qname_to_language: dict[str, str] = {}
    for language in static_analysis.get_languages():
        try:
            cfg = static_analysis.get_cfg(language)
        except ValueError:
            continue
        for node in cfg.to_networkx().nodes:
            qname_to_language.setdefault(node, language)
    return qname_to_language


def _iter_components(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
):
    yield from root_analysis.components
    for sub in sub_analyses.values():
        yield from sub.components
