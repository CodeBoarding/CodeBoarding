"""
Helper functions for working with CFG cluster analysis.

This module provides common patterns for cluster operations to reduce code duplication
across agents and other components that work with static analysis cluster results.

Super-clustering overview
-------------------------
When a language produces more clusters than `MAX_LLM_CLUSTERS`, we collapse them
into *super-clusters* via community detection on a weighted meta-graph of inter-
cluster call edges (Louvain with resolution tuning).

After community detection, there are often leftover singleton / tiny communities
because many clusters are isolated in the call graph (no inter-cluster edges).
We absorb these into larger communities using **graph distance** on the meta-graph
first. Only when a community is completely disconnected (infinite shortest-path
distance) do we fall back to **file overlap** as a proxy for relatedness.
"""

import logging
from collections import defaultdict

import networkx as nx
import networkx.algorithms.community as nx_comm

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import ClusteringConfig
from static_analyzer.graph import ClusterResult

logger = logging.getLogger(__name__)

# Maximum number of clusters the LLM should see. When a language produces
# more clusters than this, merge_clusters() collapses them into super-clusters
# using community detection on the inter-cluster connectivity graph.
MAX_LLM_CLUSTERS = 50


def build_cluster_results_for_languages(
    static_analysis: StaticAnalysisResults, languages: list[str]
) -> dict[str, ClusterResult]:
    """
    Build cluster results for specified languages.

    Args:
        static_analysis: Static analysis results containing CFG data
        languages: List of language names to build cluster results for

    Returns:
        Dictionary mapping language name -> ClusterResult
    """
    cluster_results = {}
    for lang in languages:
        cfg = static_analysis.get_cfg(lang)
        cluster_results[lang] = cfg.cluster()
    return cluster_results


def build_all_cluster_results(static_analysis: StaticAnalysisResults) -> dict[str, ClusterResult]:
    """
    Build cluster results for all detected languages in the static analysis.

    If a language produces more than MAX_LLM_CLUSTERS clusters, they are
    automatically merged into super-clusters using inter-cluster connectivity.

    Args:
        static_analysis: Static analysis results containing CFG data

    Returns:
        Dictionary mapping language name -> ClusterResult
    """
    languages = static_analysis.get_languages()
    cluster_results = build_cluster_results_for_languages(static_analysis, languages)

    for lang in list(cluster_results.keys()):
        cr = cluster_results[lang]
        n_clusters = len(cr.clusters)
        if n_clusters > MAX_LLM_CLUSTERS:
            cfg = static_analysis.get_cfg(lang)
            logger.info(
                f"[SuperCluster] {lang}: {n_clusters} clusters exceeds limit of {MAX_LLM_CLUSTERS}, "
                f"merging into super-clusters"
            )
            cluster_results[lang] = merge_clusters(cr, cfg.to_networkx(), MAX_LLM_CLUSTERS)
            new_count = len(cluster_results[lang].clusters)
            logger.info(f"[SuperCluster] {lang}: merged {n_clusters} -> {new_count} super-clusters")

    # For multi-language repos, ensure the combined cluster count stays
    # within MAX_LLM_CLUSTERS by proportionally reducing per-language counts,
    # then re-index IDs so they don't overlap across languages.
    if len(cluster_results) > 1:
        cfg_graphs = {lang: static_analysis.get_cfg(lang).to_networkx() for lang in cluster_results}
        enforce_cross_language_budget(cluster_results, cfg_graphs)

    return cluster_results


def enforce_cross_language_budget(
    cluster_results: dict[str, ClusterResult],
    cfg_graphs: dict[str, nx.DiGraph],
    target: int = MAX_LLM_CLUSTERS,
) -> None:
    """Enforce a combined cluster budget across languages and re-index IDs.

    Mutates *cluster_results* in place:
      1. If the combined cluster count exceeds *target*, proportionally reduce
         each language's count (minimum 2 per language) via ``merge_clusters``.
      2. Re-index cluster IDs with per-language offsets so they form a unique,
         non-overlapping namespace (required by downstream code that maps
         cluster_id -> component in a single dict).

    Args:
        cluster_results: Language -> ClusterResult mapping (mutated in place).
        cfg_graphs: Language -> networkx DiGraph for each language (needed by
            ``merge_clusters`` when reducing).
        target: Maximum total clusters across all languages.
    """
    if len(cluster_results) <= 1:
        return

    total_clusters = sum(len(cr.clusters) for cr in cluster_results.values())
    if total_clusters > target:
        for lang in list(cluster_results.keys()):
            cr = cluster_results[lang]
            lang_count = len(cr.clusters)
            lang_target = max(2, round(target * lang_count / total_clusters))
            if lang_count > lang_target:
                logger.info(f"[SuperCluster] {lang}: reducing {lang_count} -> {lang_target} (cross-language budget)")
                cluster_results[lang] = merge_clusters(cr, cfg_graphs[lang], lang_target)

    # Re-index so IDs don't overlap across languages
    offset = 0
    for lang in sorted(cluster_results.keys()):
        cr = cluster_results[lang]
        if offset > 0:
            cluster_results[lang] = reindex_cluster_result(cr, offset)
            logger.info(f"[ReIndex] {lang}: offset IDs by +{offset} (now {offset + 1}-{offset + len(cr.clusters)})")
        offset += len(cr.clusters)


# ---------------------------------------------------------------------------
# Meta-graph construction
# ---------------------------------------------------------------------------


def _build_node_to_cluster_lookup(cluster_result: ClusterResult) -> dict[str, int]:
    """Map each CFG node to its owning cluster ID."""
    node_to_cluster: dict[str, int] = {}
    for cluster_id, nodes in cluster_result.clusters.items():
        for node in nodes:
            node_to_cluster[node] = cluster_id
    return node_to_cluster


def _build_meta_graph(cluster_result: ClusterResult, cfg_graph: nx.DiGraph) -> nx.Graph:
    """
    Build a weighted undirected meta-graph of inter-cluster connectivity.

    Each node is a cluster ID. Edge weights represent the number of cross-cluster
    calls in the original CFG.
    """
    node_to_cluster = _build_node_to_cluster_lookup(cluster_result)

    meta_graph = nx.Graph()
    for cid in cluster_result.clusters:
        meta_graph.add_node(cid)

    edge_weights: dict[tuple[int, int], int] = defaultdict(int)
    for src, dst in cfg_graph.edges():
        src_cid = node_to_cluster.get(src)
        dst_cid = node_to_cluster.get(dst)
        if src_cid is not None and dst_cid is not None and src_cid != dst_cid:
            key = (min(src_cid, dst_cid), max(src_cid, dst_cid))
            edge_weights[key] += 1

    for (src_cid, dst_cid), weight in edge_weights.items():
        meta_graph.add_edge(src_cid, dst_cid, weight=weight)

    return meta_graph


# ---------------------------------------------------------------------------
# Community detection
# ---------------------------------------------------------------------------


def _detect_communities(meta_graph: nx.Graph, target: int, n_original: int) -> list[set[int]]:
    """
    Run Louvain community detection with resolution tuning to approach the target count.

    Falls back to connected components if Louvain fails or produces no improvement.
    """
    best_communities: list[set[int]] | None = None
    best_distance = float("inf")

    for resolution in [0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0, 5.0]:
        try:
            communities = list(
                nx_comm.louvain_communities(
                    meta_graph,
                    weight="weight",
                    resolution=resolution,
                    seed=ClusteringConfig.CLUSTERING_SEED,
                )
            )
            distance = abs(len(communities) - target)
            if distance < best_distance:
                best_distance = distance
                best_communities = communities
            logger.debug(f"[SuperCluster] resolution={resolution}: {len(communities)} communities")
        except Exception as e:
            logger.debug(f"[SuperCluster] resolution={resolution} failed: {e}")

    if best_communities is None or len(best_communities) >= n_original:
        best_communities = [set(c) for c in nx.connected_components(meta_graph)]
        logger.warning(f"[SuperCluster] Falling back to connected components: {len(best_communities)} groups")

    return best_communities


# ---------------------------------------------------------------------------
# Small-community absorption
# ---------------------------------------------------------------------------


def _community_files(community: set[int], cluster_result: ClusterResult) -> set[str]:
    """Collect all file paths touched by a community of cluster IDs."""
    files: set[str] = set()
    for cid in community:
        files.update(cluster_result.cluster_to_files.get(cid, set()))
    return files


def _find_nearest_by_graph_distance(
    smallest_idx: int,
    communities: list[set[int]],
    meta_graph: nx.Graph,
) -> int | None:
    """
    Find the community closest to *smallest_idx* by shortest-path distance
    in the meta-graph.

    For each candidate community we take the minimum shortest-path length
    between any cluster in the smallest community and any cluster in the
    candidate. Returns ``None`` when no finite path exists (disconnected).
    """
    smallest = communities[smallest_idx]
    best_idx: int | None = None
    best_dist = float("inf")

    for idx, candidate in enumerate(communities):
        if idx == smallest_idx:
            continue
        for src in smallest:
            for dst in candidate:
                try:
                    dist = nx.shortest_path_length(meta_graph, src, dst)
                except nx.NetworkXNoPath:
                    continue
                if dist < best_dist:
                    best_dist = dist
                    best_idx = idx
            if best_dist == 1:
                # Can't do better than direct neighbours – stop early.
                return best_idx

    return best_idx


def _find_nearest_by_file_overlap(
    smallest_idx: int,
    communities: list[set[int]],
    cluster_result: ClusterResult,
) -> int | None:
    """
    Fallback for disconnected communities: find the candidate with the most
    file overlap with the smallest community.
    """
    smallest_files = _community_files(communities[smallest_idx], cluster_result)
    best_idx: int | None = None
    best_overlap = -1

    for idx, candidate in enumerate(communities):
        if idx == smallest_idx:
            continue
        overlap = len(smallest_files & _community_files(candidate, cluster_result))
        if overlap > best_overlap:
            best_overlap = overlap
            best_idx = idx

    return best_idx


def reindex_cluster_result(cluster_result: ClusterResult, offset: int) -> ClusterResult:
    """Re-index all cluster IDs in a ClusterResult by adding an offset.

    Args:
        cluster_result: Original ClusterResult
        offset: Integer to add to every cluster ID

    Returns:
        New ClusterResult with shifted IDs
    """
    new_clusters: dict[int, set[str]] = {}
    new_cluster_to_files: dict[int, set[str]] = {}
    new_file_to_clusters: dict[str, set[int]] = defaultdict(set)

    for old_id, nodes in cluster_result.clusters.items():
        new_id = old_id + offset
        new_clusters[new_id] = nodes
        if old_id in cluster_result.cluster_to_files:
            new_cluster_to_files[new_id] = cluster_result.cluster_to_files[old_id]

    for file_path, old_ids in cluster_result.file_to_clusters.items():
        new_file_to_clusters[file_path] = {old_id + offset for old_id in old_ids}

    return ClusterResult(
        clusters=new_clusters,
        cluster_to_files=new_cluster_to_files,
        file_to_clusters=dict(new_file_to_clusters),
        strategy=cluster_result.strategy,
    )


def _absorb_small_communities(
    communities: list[set[int]],
    cluster_result: ClusterResult,
    meta_graph: nx.Graph,
    target: int,
) -> list[set[int]]:
    """
    Absorb small communities into larger ones until we reach *target* count.

    Merge strategy (applied repeatedly to the smallest community):
      1. **Graph distance** – merge into the community with the shortest path
         in the meta-graph. This is consistent with the Louvain step.
      2. **File overlap** – fallback for completely disconnected communities
         where no finite path exists.
    """
    result = [set(c) for c in communities]

    while len(result) > target:
        smallest_idx = min(range(len(result)), key=lambda i: len(result[i]))

        # Prefer graph distance; fall back to file overlap for disconnected clusters.
        merge_idx = _find_nearest_by_graph_distance(smallest_idx, result, meta_graph)
        if merge_idx is None:
            merge_idx = _find_nearest_by_file_overlap(smallest_idx, result, cluster_result)

        if merge_idx is None:
            break

        result[merge_idx].update(result[smallest_idx])
        result.pop(smallest_idx)

    logger.info(f"[SuperCluster] Absorbed small communities: {len(communities)} -> {len(result)}")
    return result


# ---------------------------------------------------------------------------
# ClusterResult assembly
# ---------------------------------------------------------------------------


def _build_merged_cluster_result(
    communities: list[set[int]],
    cluster_result: ClusterResult,
    cfg_graph: nx.DiGraph,
) -> ClusterResult:
    """
    Build a new ClusterResult by merging original clusters according to
    the given community assignments, re-indexed from 1..N (largest first).
    """
    # Sort super-clusters by total node count (largest first) for consistent ordering.
    sorted_communities = sorted(
        communities,
        key=lambda sc: sum(len(cluster_result.clusters.get(cid, set())) for cid in sc),
        reverse=True,
    )

    new_clusters: dict[int, set[str]] = {}
    new_cluster_to_files: dict[int, set[str]] = defaultdict(set)
    new_file_to_clusters: dict[str, set[int]] = defaultdict(set)

    for new_id, old_cids in enumerate(sorted_communities, start=1):
        merged_nodes: set[str] = set()
        for old_cid in old_cids:
            merged_nodes.update(cluster_result.clusters.get(old_cid, set()))
        new_clusters[new_id] = merged_nodes

        for node in merged_nodes:
            node_data = cfg_graph.nodes.get(node, {})
            file_path = node_data.get("file_path")
            if file_path:
                new_cluster_to_files[new_id].add(file_path)
                new_file_to_clusters[file_path].add(new_id)

    return ClusterResult(
        clusters=new_clusters,
        cluster_to_files=dict(new_cluster_to_files),
        file_to_clusters=dict(new_file_to_clusters),
        strategy=f"super_{cluster_result.strategy}",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def merge_clusters(
    cluster_result: ClusterResult,
    cfg_graph: nx.DiGraph,
    target: int = MAX_LLM_CLUSTERS,
) -> ClusterResult:
    """
    Merge clusters into super-clusters using community detection on the
    inter-cluster connectivity graph.

    Pipeline:
      1. Build a weighted meta-graph (nodes = cluster IDs, edge weights =
         number of cross-cluster calls).
      2. Run Louvain community detection at several resolutions, picking the
         result closest to *target*.
      3. Absorb leftover small / singleton communities – first by graph
         distance, then by file overlap for disconnected ones.
      4. Re-index the super-clusters from 1..N.

    Args:
        cluster_result: Original ClusterResult with too many clusters
        cfg_graph: The networkx DiGraph of the full call graph
        target: Target maximum number of super-clusters

    Returns:
        New ClusterResult with merged clusters and re-indexed IDs (1..N)
    """
    n_original = len(cluster_result.clusters)

    meta_graph = _build_meta_graph(cluster_result, cfg_graph)
    communities = _detect_communities(meta_graph, target, n_original)

    if len(communities) > target:
        communities = _absorb_small_communities(communities, cluster_result, meta_graph, target)

    logger.info(
        f"[SuperCluster] Merged {n_original} clusters into {len(communities)} super-clusters " f"(target was {target})"
    )

    return _build_merged_cluster_result(communities, cluster_result, cfg_graph)


# ---------------------------------------------------------------------------
# Cluster ID / file helpers
# ---------------------------------------------------------------------------


def get_all_cluster_ids(cluster_results: dict[str, ClusterResult]) -> set[int]:
    """
    Get all cluster IDs from cluster results across all languages.

    Args:
        cluster_results: Dictionary mapping language -> ClusterResult

    Returns:
        Set of all cluster IDs found across all languages
    """
    cluster_ids = set()
    for cluster_result in cluster_results.values():
        cluster_ids.update(cluster_result.get_cluster_ids())
    return cluster_ids


def get_files_for_cluster_ids(cluster_ids: list[int], cluster_results: dict[str, ClusterResult]) -> set[str]:
    """
    Get all files that belong to the specified cluster IDs across all languages.

    Args:
        cluster_ids: List of cluster IDs to get files for
        cluster_results: Dictionary mapping language -> ClusterResult

    Returns:
        Set of file paths belonging to the specified clusters
    """
    files: set[str] = set()
    for cluster_result in cluster_results.values():
        for cluster_id in cluster_ids:
            files.update(cluster_result.get_files_for_cluster(cluster_id))
    return files
