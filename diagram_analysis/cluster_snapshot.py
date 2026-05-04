"""In-memory cluster snapshot reconstructed from ``analysis.json``.

The prior clustering is persisted inline on each ``Component`` as
``cluster_members``. This module reads it back: walks the loaded
``AnalysisInsights`` tree, collects per-component members, and partitions
them by programming language using CFG-derived file paths.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from agents.agent_responses import AnalysisInsights, iter_components
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


def snapshot_from_analysis(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    static_analysis: StaticAnalysisResults,
) -> ClusterSnapshot:
    """Reconstruct a ``ClusterSnapshot`` from the loaded analysis tree.

    Bucket each qname by language: prefer the fresh CFG; fall back to
    file-extension lookup against the qname's prior ``file_methods`` path.
    Keep deleted/drifted qnames in the snapshot so ``compute_cluster_delta``
    sees them as ``removed_nodes`` — drift filtering is its job, not ours.
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

    # Build prior qname -> file_path from persisted file_methods. Skip
    # duplicate component objects surfacing from both root and sub-analyses.
    qname_to_file_old: dict[str, str] = {}
    seen_components_for_files: set[int] = set()
    for component in iter_components(root_analysis, sub_analyses):
        if id(component) in seen_components_for_files:
            continue
        seen_components_for_files.add(id(component))
        for group in component.file_methods:
            for method in group.methods:
                qname_to_file_old.setdefault(method.qualified_name, group.file_path)

    by_language: dict[str, dict[int, ClusterSnapshotEntry]] = defaultdict(dict)
    seen_components: set[int] = set()
    unbucketed_qnames = 0
    for component in iter_components(root_analysis, sub_analyses):
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
                    # No fresh CFG entry and no file_methods to derive a
                    # language from — drop so the delta isn't poisoned.
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
