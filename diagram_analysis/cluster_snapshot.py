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

from agents.agent_responses import AnalysisInsights
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import ClusterResult

logger = logging.getLogger(__name__)


@dataclass
class ClusterSnapshotEntry:
    members: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)


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
    ``Component.cluster_members``, and partitions the result by language
    using the freshly-built CFG node attributes (each CFG node carries its
    ``file_path``; we use that to bucket every qualified name into the
    language whose CFG owns it).

    Members that aren't present in any current CFG node are skipped — they
    correspond to deleted code, and including them would defeat the
    delta logic that already infers deletions from the universe diff.
    """
    qname_to_language = _build_qname_language_index(static_analysis)
    qname_to_file: dict[str, str] = {}
    for language in static_analysis.get_languages():
        try:
            cfg = static_analysis.get_cfg(language)
        except ValueError:
            continue
        for node, attrs in cfg.to_networkx().nodes(data=True):
            file_path = attrs.get("file_path")
            if file_path:
                qname_to_file.setdefault(node, file_path)

    by_language: dict[str, dict[int, ClusterSnapshotEntry]] = defaultdict(dict)
    seen_components: set[int] = set()
    for component in _iter_components(root_analysis, sub_analyses):
        if id(component) in seen_components:
            continue
        seen_components.add(id(component))
        for cid, qnames in component.cluster_members.items():
            for qname in qnames:
                language = qname_to_language.get(qname)
                if language is None:
                    # Member was deleted from the source between analyses;
                    # ignore it so the delta picks up the absence cleanly.
                    continue
                lang_entries = by_language[language]
                entry = lang_entries.get(cid)
                if entry is None:
                    entry = ClusterSnapshotEntry()
                    lang_entries[cid] = entry
                entry.members.add(qname)
                file_path = qname_to_file.get(qname)
                if file_path:
                    entry.files.add(file_path)

    return ClusterSnapshot(by_language=dict(by_language))


def snapshot_from_cluster_results(cluster_results: dict[str, ClusterResult]) -> ClusterSnapshot:
    """Build an in-memory snapshot directly from cluster results (used by tests)."""
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
