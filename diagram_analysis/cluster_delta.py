"""Deterministic cluster delta computation.

Mirrors ``build_all_cluster_results`` for the incremental path: produces a
``ClusterDelta`` describing which clusters carried over, which changed members,
which are entirely new, and which dropped — without requiring any LLM call.

Flavor B (default, iterative): drop deleted nodes from each cluster, route
added nodes to the cluster they share the most CFG edges with, and run Louvain
on the leftover induced subgraph for any added nodes that don't fit anywhere.
This preserves cluster identity perfectly when Louvain communities are stable.

Flavor A (fallback, triggered when ``changed_pct > FULL_RECLUSTER_THRESHOLD``):
re-run ``build_all_cluster_results`` end-to-end and match by member-Jaccard
against the old snapshot. Used when the diff is so large that incremental
routing would produce noise.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import networkx.algorithms.community as nx_comm

from diagram_analysis.cluster_snapshot import ClusterSnapshot, ClusterSnapshotEntry
from diagram_analysis.io_utils import normalize_repo_path
from repo_utils.change_detector import ChangeSet
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import build_all_cluster_results
from static_analyzer.graph import ClusterResult

logger = logging.getLogger(__name__)

FULL_RECLUSTER_THRESHOLD = 0.25
JACCARD_MATCH_THRESHOLD = 0.5


@dataclass
class LanguageDelta:
    language: str
    cluster_results: ClusterResult
    new_cluster_ids: set[int] = field(default_factory=set)
    changed_cluster_ids: set[int] = field(default_factory=set)
    dropped_cluster_ids: set[int] = field(default_factory=set)
    cluster_id_remap: dict[int, int] = field(default_factory=dict)
    fallback_used: bool = False

    @property
    def affected_cluster_ids(self) -> set[int]:
        return self.new_cluster_ids | self.changed_cluster_ids


@dataclass
class ClusterDelta:
    by_language: dict[str, LanguageDelta] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return any(d.affected_cluster_ids or d.dropped_cluster_ids for d in self.by_language.values())

    def all_affected_cluster_ids(self) -> set[int]:
        return {cid for d in self.by_language.values() for cid in d.affected_cluster_ids}

    def all_dropped_cluster_ids(self) -> set[int]:
        return {cid for d in self.by_language.values() for cid in d.dropped_cluster_ids}

    def cluster_results(self) -> dict[str, ClusterResult]:
        return {lang: d.cluster_results for lang, d in self.by_language.items()}

    def merged_cluster_id_remap(self) -> dict[int, int]:
        merged: dict[int, int] = {}
        for d in self.by_language.values():
            merged.update(d.cluster_id_remap)
        return merged


def compute_cluster_delta(
    old_snapshot: ClusterSnapshot,
    new_static: StaticAnalysisResults,
    threshold: float = FULL_RECLUSTER_THRESHOLD,
    changes: ChangeSet | None = None,
    repo_dir: Path | None = None,
) -> ClusterDelta:
    """Compute per-language cluster deltas. See module docstring for flavor details.

    When ``changes`` is provided, the cluster delta filters drift noise:
    qnames whose file is outside the source diff AND outside the prior analysis
    are dropped, since neither side considers them tracked changes. Qnames in
    the prior analysis that vanish without their file appearing in the diff
    are kept (the LLM should reason about the inconsistency) but logged as a
    warning so drift is visible.

    ``repo_dir`` is used to normalize CFG-absolute paths down to repo-relative
    posix so they can be compared against the diff's repo-relative paths.

    When ``changes`` is ``None`` (e.g., GitHub Action callers without a diff
    source), no scoping is applied — current behavior.
    """
    fresh_clusters = build_all_cluster_results(new_static)
    delta = ClusterDelta()
    diff_files = _changeset_to_path_set(changes) if changes is not None else None
    for language in new_static.get_languages():
        cfg = new_static.get_cfg(language)
        nx_graph = cfg.to_networkx()
        old_clusters = old_snapshot.get_language(language)
        fresh_for_lang = fresh_clusters.get(language, ClusterResult())
        delta.by_language[language] = _delta_for_language(
            language, nx_graph, old_clusters, fresh_for_lang, threshold, diff_files, repo_dir
        )
    return delta


def _changeset_to_path_set(changes: ChangeSet) -> set[str]:
    """Collect every path mentioned by a ChangeSet — including renames' old paths.

    Renames are an edge case: ``FileChange.file_path`` is the new path,
    ``FileChange.old_path`` the old. A qname under the old path that
    survives in the fresh CFG would otherwise look like drift; including
    both sides keeps that case in the diff scope.
    """
    paths: set[str] = set()
    for fc in changes.files:
        paths.add(fc.file_path)
        if fc.old_path:
            paths.add(fc.old_path)
    return paths


def _delta_for_language(
    language: str,
    nx_graph: nx.DiGraph,
    old_clusters: dict[int, ClusterSnapshotEntry],
    fresh_for_lang: ClusterResult,
    threshold: float,
    diff_files: set[str] | None = None,
    repo_dir: Path | None = None,
) -> LanguageDelta:
    # Universe is the set of nodes ACTUALLY assigned to a cluster in either
    # the prior snapshot or the fresh clustering. ``cfg.cluster()`` filters
    # out singleton/noise nodes that ``nx_graph.nodes`` still includes; if
    # we used the raw graph as the universe, those noise nodes would always
    # look "added" -> would always trigger spurious Flavor B reroutes ->
    # ``has_changes`` would never be False even on a no-op refresh.
    fresh_member_union = {qname for members in fresh_for_lang.clusters.values() for qname in members}
    old_member_union = {qname for entry in old_clusters.values() for qname in entry.members}

    universe = fresh_member_union | old_member_union
    if not universe:
        return LanguageDelta(language=language, cluster_results=fresh_for_lang)

    raw_added = fresh_member_union - old_member_union
    raw_removed = old_member_union - fresh_member_union

    # Diff scoping: see compute_cluster_delta docstring. We drop only the
    # quadrant (qname not-in prior analysis AND file not-in diff) — pure
    # drift on untracked qnames in untracked files. Quadrant (qname
    # in-prior-analysis AND file not-in diff) is preserved as inconsistent
    # state (logged as warning).
    inconsistent_removed: set[str] = set()
    if diff_files is not None:

        def _fresh_file(qname: str) -> str | None:
            attrs = nx_graph.nodes.get(qname)
            if attrs is None:
                return None
            file_path = attrs.get("file_path")
            return normalize_repo_path(file_path, repo_dir) if file_path else None

        def _old_file(qname: str) -> str | None:
            for entry in old_clusters.values():
                fp = entry.member_files.get(qname)
                if fp:
                    return normalize_repo_path(fp, repo_dir)
            return None

        scoped_added: set[str] = set()
        for qname in raw_added:
            file_path = _fresh_file(qname)
            if file_path is not None and file_path in diff_files:
                scoped_added.add(qname)
            # else: not-in analysis, not-in diff -- drift, drop.
        added_nodes = scoped_added

        # Removed quadrant: keep all (so Flavor B clears their clusters), but
        # surface the inconsistent ones in the log.
        for qname in raw_removed:
            file_path = _old_file(qname)
            if file_path is None or file_path not in diff_files:
                inconsistent_removed.add(qname)
        removed_nodes = raw_removed
    else:
        added_nodes = raw_added
        removed_nodes = raw_removed

    changed_pct = (len(added_nodes) + len(removed_nodes)) / len(universe)

    if changed_pct > threshold:
        logger.info(
            f"[cluster_delta] {language}: changed_pct={changed_pct:.3f} > {threshold}, "
            "falling back to full re-cluster."
        )
        return _flavor_a_fallback(language, fresh_for_lang, old_clusters)

    logger.info(
        "[cluster_delta] %s: Flavor B raw=(added=%d, removed=%d); "
        "diff-scoped=(added=%d, removed=%d); inconsistent=%d; changed_pct=%.3f",
        language,
        len(raw_added),
        len(raw_removed),
        len(added_nodes),
        len(removed_nodes),
        len(inconsistent_removed),
        changed_pct,
    )
    # Sample of the qnames going into / out of the universe — small enough to
    # log every run, useful for diagnosing "why is the LLM call firing on a
    # pure deletion" scenarios where added/removed counts disagree with the
    # user-visible diff.
    if added_nodes or removed_nodes:
        logger.info(
            "[cluster_delta] %s added qnames (first 20): %s; removed qnames (first 20): %s",
            language,
            sorted(added_nodes)[:20],
            sorted(removed_nodes)[:20],
        )
    if inconsistent_removed:
        logger.warning(
            "[cluster_delta] %s removed qnames outside source diff (inconsistent, %d total, first 10): %s",
            language,
            len(inconsistent_removed),
            sorted(inconsistent_removed)[:10],
        )
    return _flavor_b_iterative(language, nx_graph, old_clusters, added_nodes, removed_nodes)


# ---------------------------------------------------------------------------
# Flavor B
# ---------------------------------------------------------------------------
def _flavor_b_iterative(
    language: str,
    nx_graph: nx.DiGraph,
    old_clusters: dict[int, ClusterSnapshotEntry],
    added_nodes: set[str],
    removed_nodes: set[str],
) -> LanguageDelta:
    new_member_sets: dict[int, set[str]] = {}
    for cid, entry in old_clusters.items():
        new_member_sets[cid] = set(entry.members) - removed_nodes

    changed_cluster_ids: set[int] = set()
    if removed_nodes:
        changed_cluster_ids |= {cid for cid, entry in old_clusters.items() if entry.members & removed_nodes}

    undirected = nx_graph.to_undirected()
    node_to_cid = _invert_clusters(new_member_sets)

    unassigned: list[str] = []
    for node in sorted(added_nodes):
        target_cid = _argmax_neighbor_cluster(node, undirected, node_to_cid)
        if target_cid is None:
            unassigned.append(node)
        else:
            new_member_sets.setdefault(target_cid, set()).add(node)
            node_to_cid[node] = target_cid
            changed_cluster_ids.add(target_cid)

    new_cluster_ids: set[int] = set()
    if unassigned:
        next_id = max(new_member_sets.keys(), default=0) + 1
        for community in _louvain_unassigned(undirected.subgraph(unassigned)):
            new_member_sets[next_id] = set(community)
            new_cluster_ids.add(next_id)
            for node in community:
                node_to_cid[node] = next_id
            next_id += 1

    dropped_cluster_ids = {cid for cid, members in new_member_sets.items() if not members}
    for cid in dropped_cluster_ids:
        del new_member_sets[cid]
    changed_cluster_ids -= dropped_cluster_ids

    cluster_results = _materialize_cluster_result(new_member_sets, nx_graph, "incremental_b")
    cluster_id_remap = {cid: cid for cid in old_clusters.keys() if cid in new_member_sets}

    return LanguageDelta(
        language=language,
        cluster_results=cluster_results,
        new_cluster_ids=new_cluster_ids,
        changed_cluster_ids=changed_cluster_ids,
        dropped_cluster_ids=dropped_cluster_ids,
        cluster_id_remap=cluster_id_remap,
        fallback_used=False,
    )


def _invert_clusters(member_sets: dict[int, set[str]]) -> dict[str, int]:
    return {qname: cid for cid, members in member_sets.items() for qname in members}


def _argmax_neighbor_cluster(
    node: str,
    undirected: nx.Graph,
    node_to_cid: dict[str, int],
) -> int | None:
    """Return the cluster ID with the most edges to ``node``; tie-break by file co-location."""
    if node not in undirected:
        return None
    counts: dict[int, int] = {}
    for neighbor in undirected.neighbors(node):
        cid = node_to_cid.get(neighbor)
        if cid is None:
            continue
        counts[cid] = counts.get(cid, 0) + 1
    if not counts:
        return _file_overlap_fallback(node, undirected, node_to_cid)
    best = max(counts.values())
    candidates = [cid for cid, count in counts.items() if count == best]
    if len(candidates) == 1:
        return candidates[0]
    return _file_overlap_fallback(node, undirected, node_to_cid, restrict_to=set(candidates)) or candidates[0]


def _file_overlap_fallback(
    node: str,
    undirected: nx.Graph,
    node_to_cid: dict[str, int],
    restrict_to: set[int] | None = None,
) -> int | None:
    """Tie-break by file path: pick the cluster with most members in the same file."""
    file_path = undirected.nodes[node].get("file_path") if node in undirected else None
    if not file_path:
        return None
    counts: dict[int, int] = {}
    for other, attrs in undirected.nodes(data=True):
        if attrs.get("file_path") != file_path or other == node:
            continue
        cid = node_to_cid.get(other)
        if cid is None or (restrict_to and cid not in restrict_to):
            continue
        counts[cid] = counts.get(cid, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _louvain_unassigned(subgraph: nx.Graph) -> list[set[str]]:
    if subgraph.number_of_nodes() == 0:
        return []
    if subgraph.number_of_edges() == 0:
        return [{node} for node in subgraph.nodes]
    try:
        communities = nx_comm.louvain_communities(subgraph, seed=42)
    except Exception as e:
        logger.warning(f"Louvain on unassigned subgraph failed ({e}); falling back to connected components.")
        communities = nx.connected_components(subgraph)
    return [set(c) for c in communities if c]


def _materialize_cluster_result(
    member_sets: dict[int, set[str]],
    nx_graph: nx.DiGraph,
    strategy: str,
) -> ClusterResult:
    clusters: dict[int, set[str]] = {}
    cluster_to_files: dict[int, set[str]] = {}
    file_to_clusters: dict[str, set[int]] = {}
    for cid, members in member_sets.items():
        if not members:
            continue
        clusters[cid] = set(members)
        files: set[str] = set()
        for qname in members:
            attrs = nx_graph.nodes.get(qname, {})
            file_path = attrs.get("file_path")
            if file_path:
                files.add(file_path)
                file_to_clusters.setdefault(file_path, set()).add(cid)
        cluster_to_files[cid] = files
    return ClusterResult(
        clusters=clusters,
        cluster_to_files=cluster_to_files,
        file_to_clusters=file_to_clusters,
        strategy=strategy,
    )


# ---------------------------------------------------------------------------
# Flavor A
# ---------------------------------------------------------------------------
def _flavor_a_fallback(
    language: str,
    fresh_for_lang: ClusterResult,
    old_clusters: dict[int, ClusterSnapshotEntry],
) -> LanguageDelta:
    cluster_id_remap, matched_old_ids = _match_by_jaccard(old_clusters, fresh_for_lang.clusters)

    new_cluster_ids: set[int] = set()
    changed_cluster_ids: set[int] = set()
    for new_cid in fresh_for_lang.clusters:
        if new_cid in cluster_id_remap.values():
            old_cid = next(o for o, n in cluster_id_remap.items() if n == new_cid)
            if old_clusters[old_cid].members != fresh_for_lang.clusters[new_cid]:
                changed_cluster_ids.add(new_cid)
        else:
            new_cluster_ids.add(new_cid)

    dropped_cluster_ids = {cid for cid in old_clusters.keys() if cid not in matched_old_ids}

    return LanguageDelta(
        language=language,
        cluster_results=fresh_for_lang,
        new_cluster_ids=new_cluster_ids,
        changed_cluster_ids=changed_cluster_ids,
        dropped_cluster_ids=dropped_cluster_ids,
        cluster_id_remap=cluster_id_remap,
        fallback_used=True,
    )


def _match_by_jaccard(
    old_clusters: dict[int, ClusterSnapshotEntry],
    new_clusters: dict[int, set[str]],
) -> tuple[dict[int, int], set[int]]:
    """Greedy 1:1 match by max Jaccard >= threshold; returns (old->new remap, matched_old_ids)."""
    pairs: list[tuple[float, int, int]] = []
    for old_cid, entry in old_clusters.items():
        for new_cid, new_members in new_clusters.items():
            score = _jaccard(entry.members, new_members)
            if score >= JACCARD_MATCH_THRESHOLD:
                pairs.append((score, old_cid, new_cid))
    pairs.sort(reverse=True)

    remap: dict[int, int] = {}
    used_new: set[int] = set()
    matched_old: set[int] = set()
    for _, old_cid, new_cid in pairs:
        if old_cid in matched_old or new_cid in used_new:
            continue
        remap[old_cid] = new_cid
        matched_old.add(old_cid)
        used_new.add(new_cid)
    return remap, matched_old


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0
