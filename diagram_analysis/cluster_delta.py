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

import networkx as nx
import networkx.algorithms.community as nx_comm

from diagram_analysis.cluster_snapshot import ClusterSnapshot, ClusterSnapshotEntry
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
) -> ClusterDelta:
    """Compute per-language cluster deltas. See module docstring for flavor details."""
    fresh_clusters = build_all_cluster_results(new_static)
    delta = ClusterDelta()
    for language in new_static.get_languages():
        cfg = new_static.get_cfg(language)
        nx_graph = cfg.to_networkx()
        old_clusters = old_snapshot.get_language(language)
        fresh_for_lang = fresh_clusters.get(language, ClusterResult())
        delta.by_language[language] = _delta_for_language(language, nx_graph, old_clusters, fresh_for_lang, threshold)
    return delta


def _delta_for_language(
    language: str,
    nx_graph: nx.DiGraph,
    old_clusters: dict[int, ClusterSnapshotEntry],
    fresh_for_lang: ClusterResult,
    threshold: float,
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

    added_nodes = fresh_member_union - old_member_union
    removed_nodes = old_member_union - fresh_member_union
    changed_pct = (len(added_nodes) + len(removed_nodes)) / len(universe)

    if changed_pct > threshold:
        logger.info(
            f"[cluster_delta] {language}: changed_pct={changed_pct:.3f} > {threshold}, "
            "falling back to full re-cluster."
        )
        return _flavor_a_fallback(language, fresh_for_lang, old_clusters)

    logger.info(
        f"[cluster_delta] {language}: Flavor B "
        f"(added={len(added_nodes)}, removed={len(removed_nodes)}, changed_pct={changed_pct:.3f})"
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
