"""
Helper functions for working with CFG cluster analysis.

This module provides common patterns for cluster operations to reduce code duplication
across agents and other components that work with static analysis cluster results.

Super-clustering overview
-------------------------
When a language produces more clusters than `MAX_LLM_CLUSTERS`, we collapse them
into *super-clusters* via community detection on a weighted meta-graph of inter-
cluster call edges (Leiden with resolution tuning, Louvain fallback).

After community detection, there are often leftover singleton / tiny communities
because many clusters are isolated in the call graph (no inter-cluster edges).
We absorb these into larger communities using **graph distance** on the meta-graph
first. Only when a community is completely disconnected (infinite shortest-path
distance) do we fall back to **file overlap** as a proxy for relatedness.
"""

import logging
import os
from collections import defaultdict

import networkx as nx
import networkx.algorithms.community as nx_comm

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import ClusteringConfig, Language
from static_analyzer.graph import ClusterResult, detect_communities

logger = logging.getLogger(__name__)

# Maximum number of clusters the LLM should see. When a language produces
# more clusters than this, merge_clusters() collapses them into super-clusters
# using community detection on the inter-cluster connectivity graph.
MAX_LLM_CLUSTERS = 50

# Range for the number of top-level architecture components. The exact count N
# inside this range is chosen deterministically by the modularity peak of a
# resolution-tuned Leiden partition (see ``supercluster_leaf_ids``), not by the
# LLM — so the component structure is stable across re-runs.
TOP_LEVEL_COMPONENTS_MIN = 5
TOP_LEVEL_COMPONENTS_MAX = 8

# Same idea for a component's sub-components (one level down); a component is
# usually smaller than the whole repo, so the floor is lower.
SUBCOMPONENTS_MIN = 3
SUBCOMPONENTS_MAX = 8

# Resolution ladder swept to steer Leiden toward a target community count.
# Ascending: higher resolution -> more, finer communities. Reaches well past 1.0
# so a graph with a large connected core can still be over-segmented to any N in
# the target range before absorbing leftovers back down.
_RESOLUTION_LADDER = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0, 10.0)


def build_cluster_results_for_languages(
    static_analysis: StaticAnalysisResults, languages: list[Language]
) -> dict[str, ClusterResult]:
    """
    Build cluster results for specified languages.

    Args:
        static_analysis: Static analysis results containing CFG data
        languages: List of language names to build cluster results for

    Returns:
        Dictionary mapping language name -> ClusterResult
    """
    cluster_results: dict[str, ClusterResult] = {}
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
            cfg = static_analysis.get_cfg(Language(lang))
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
        cfg_graphs = {lang: static_analysis.get_cfg(Language(lang)).to_networkx() for lang in cluster_results}
        enforce_cross_language_budget(cluster_results, cfg_graphs)

    _sync_cluster_cache(static_analysis, cluster_results)
    return cluster_results


def _sync_cluster_cache(static_analysis: StaticAnalysisResults, cluster_results: dict[str, ClusterResult]) -> None:
    """Keep each CFG cache aligned with returned cluster IDs."""
    for lang, result in cluster_results.items():
        try:
            cfg = static_analysis.get_cfg(Language(lang))
            cfg._cluster_cache = result
            cfg.record_cluster_paths(result)
        except ValueError:
            logger.warning("Could not sync cluster cache for missing language %s", lang)


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


def _build_meta_graph(cluster_result: ClusterResult, cfg_graph: nx.DiGraph) -> nx.DiGraph:
    """Build a weighted directed meta-graph of inter-cluster connectivity.

    Each node is a cluster ID. Each edge ``(src_cid, dst_cid)`` carries the
    number of CFG calls from ``src_cid`` members into ``dst_cid`` members.
    Mutual coupling A<->B becomes two separate edges, each contributing
    independently to directed Leicht-Newman modularity (decision #15).
    """
    node_to_cluster = _build_node_to_cluster_lookup(cluster_result)

    meta_graph = nx.DiGraph()
    for cid in cluster_result.clusters:
        meta_graph.add_node(cid)

    edge_weights: dict[tuple[int, int], int] = defaultdict(int)
    for src, dst in cfg_graph.edges():
        src_cid = node_to_cluster.get(src)
        dst_cid = node_to_cluster.get(dst)
        if src_cid is not None and dst_cid is not None and src_cid != dst_cid:
            edge_weights[(src_cid, dst_cid)] += 1

    for (src_cid, dst_cid), weight in edge_weights.items():
        meta_graph.add_edge(src_cid, dst_cid, weight=weight)

    return meta_graph


# ---------------------------------------------------------------------------
# Community detection
# ---------------------------------------------------------------------------


def _detect_communities(meta_graph: nx.Graph | nx.DiGraph, target: int, n_original: int) -> list[set[int]]:
    """
    Run Leiden community detection (Louvain fallback) with resolution tuning to approach the target count.

    Falls back to connected components if community detection fails or produces no improvement.
    """
    best_communities: list[set[int]] | None = None
    best_distance = float("inf")

    for resolution in [0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0, 5.0]:
        try:
            communities: list[set[int]] = detect_communities(
                meta_graph,
                weight="weight",
                resolution=resolution,
                seed=ClusteringConfig.CLUSTERING_SEED,
            )
            distance = abs(len(communities) - target)
            if distance < best_distance:
                best_distance = distance
                best_communities = communities
            logger.debug(f"[SuperCluster] resolution={resolution}: {len(communities)} communities")
        except Exception as e:
            logger.debug(f"[SuperCluster] resolution={resolution} failed: {e}")

    if best_communities is None or len(best_communities) >= n_original:
        # Why weakly_connected_components: under directed meta-graphs (decision #15),
        # nx.connected_components is undefined. Reachability-ignoring-direction is
        # the right semantic for a structural-isolation safety net.
        components_iter = (
            nx.weakly_connected_components(meta_graph)
            if meta_graph.is_directed()
            else nx.connected_components(meta_graph)
        )
        best_communities = [set(c) for c in components_iter]
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
    meta_graph: nx.Graph | nx.DiGraph,
) -> int | None:
    """
    Find the community closest to *smallest_idx* by shortest-path distance
    in the meta-graph.

    For each candidate community we take the minimum shortest-path length
    between any cluster in the smallest community and any cluster in the
    candidate. Returns ``None`` when no finite path exists (disconnected).

    Distance is computed on an undirected view: absorption is about
    topological proximity, not directional reachability — a tiny utility
    cluster should be absorbable by a nearby cluster regardless of which
    way the calls flow.
    """
    smallest = communities[smallest_idx]
    best_idx: int | None = None
    best_dist = float("inf")

    distance_graph = meta_graph.to_undirected(as_view=True) if meta_graph.is_directed() else meta_graph

    for idx, candidate in enumerate(communities):
        if idx == smallest_idx:
            continue
        for src in smallest:
            for dst in candidate:
                try:
                    dist = nx.shortest_path_length(distance_graph, src, dst)
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
    meta_graph: nx.Graph | nx.DiGraph,
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
# Top-level component grouping (resolution-tuned Leiden, modularity-peak N)
# ---------------------------------------------------------------------------


def _combine_cluster_results(cluster_results: dict[str, ClusterResult]) -> ClusterResult:
    """Union per-language ClusterResults into one.

    Cluster IDs are already globally unique across languages (``build_all_cluster_results``
    re-indexes them), so a plain union is safe and lets us super-cluster every
    language's leaf clusters against a single meta-graph.
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


def _pick_peak_partition(
    meta_graph: nx.DiGraph,
    low: int,
    high: int,
    seed: int,
) -> list[set[int]]:
    """Sweep the resolution ladder and return the partition at the modularity peak in ``[low, high]``.

    "N" counts *non-singleton* communities (size >= 2 leaf clusters) — the
    connected structure Leiden actually resolves — because real call graphs
    carry a long tail of isolated leaf clusters that would otherwise pin the raw
    community count far above ``high`` at every resolution. Among partitions
    whose non-singleton count lands in range, the highest-modularity one wins.
    Falls back to the partition whose count is closest to the range when none
    lands inside it (or to all-singletons on an edgeless graph).
    """
    if meta_graph.number_of_edges() == 0:
        return [{cid} for cid in meta_graph.nodes]

    candidates: list[tuple[int, float, list[set[int]]]] = []
    for resolution in _RESOLUTION_LADDER:
        try:
            communities: list[set[int]] = detect_communities(
                meta_graph, weight="weight", resolution=resolution, seed=seed
            )
        except Exception as e:  # noqa: BLE001 - a bad resolution shouldn't abort the sweep
            logger.debug(f"[SuperCluster] resolution={resolution} failed: {e}")
            continue
        n_real = sum(1 for community in communities if len(community) >= 2)
        modularity = nx_comm.modularity(meta_graph, communities, weight="weight")
        candidates.append((n_real, modularity, communities))
        logger.debug(f"[SuperCluster] resolution={resolution}: n_real={n_real} modularity={modularity:.4f}")

    if not candidates:
        return [{cid} for cid in meta_graph.nodes]

    in_range = [c for c in candidates if low <= c[0] <= high]
    if in_range:
        n_real, modularity, communities = max(in_range, key=lambda c: c[1])
        logger.info(f"[SuperCluster] modularity peak at N={n_real} (modularity={modularity:.4f}) over [{low},{high}]")
        return communities

    def range_distance(n_real: int) -> int:
        return 0 if low <= n_real <= high else min(abs(n_real - low), abs(n_real - high))

    n_real, _, communities = min(candidates, key=lambda c: (range_distance(c[0]), -c[1]))
    logger.info(f"[SuperCluster] no partition with N in [{low},{high}]; using closest (N={n_real})")
    return communities


def _seeds_from_partition(
    communities: list[set[int]],
    method_count: dict[int, int],
    low: int,
    high: int,
) -> tuple[list[set[int]], list[int]]:
    """Split a partition into top-level *seed* communities and leftover leaf clusters.

    Seeds are the non-singleton communities (ranked by total method count),
    capped at ``high``. When there are fewer than ``low`` seeds, the largest
    leftover leaf clusters are promoted to their own seed so a genuinely big but
    call-isolated module (e.g. a data-model file nothing calls) still becomes a
    component instead of being folded into another. Everything else is a
    leftover to be absorbed.
    """
    reals = sorted(
        (set(c) for c in communities if len(c) >= 2),
        key=lambda community: (sum(method_count.get(cid, 0) for cid in community), -min(community)),
        reverse=True,
    )
    leftovers = [cid for c in communities if len(c) == 1 for cid in c]

    if len(reals) > high:
        leftovers.extend(cid for community in reals[high:] for cid in community)
        reals = reals[:high]

    # Absorption grows seed packages as it goes (order matters), so order leftovers
    # deterministically — biggest clusters first (they anchor packages), tie-broken
    # by id — rather than relying on set/community iteration order.
    leftovers.sort(key=lambda cid: (-method_count.get(cid, 0), cid))

    seeds = reals
    if len(seeds) < low:
        while len(seeds) < low and leftovers:
            seeds.append({leftovers.pop(0)})

    return seeds, leftovers


def _nearest_seed_index(
    cid: int,
    seeds: list[set[int]],
    undirected_meta: nx.Graph,
    seed_packages: list[set[str]],
    cluster_result: ClusterResult,
    method_count: dict[int, int],
) -> int:
    """Pick the seed a leftover leaf cluster belongs to: call proximity, then package, then size.

    1. Shortest meta-graph path to any seed member (call coupling).
    2. Directory/package overlap — the right signal for call-isolated clusters,
       which have no meta-graph path at all.
    3. The largest seed by method count, so nothing is dropped.
    """
    best_idx, best_dist = None, float("inf")
    for idx, seed in enumerate(seeds):
        for member in seed:
            try:
                dist = nx.shortest_path_length(undirected_meta, cid, member)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue
            if dist < best_dist:
                best_dist, best_idx = dist, idx
    if best_idx is not None:
        return best_idx

    packages = _cluster_packages(cid, cluster_result)
    best_idx, best_overlap = None, 0
    for idx, seed_pkgs in enumerate(seed_packages):
        overlap = len(packages & seed_pkgs)
        if overlap > best_overlap:
            best_overlap, best_idx = overlap, idx
    if best_idx is not None:
        return best_idx

    return max(range(len(seeds)), key=lambda idx: sum(method_count.get(cid, 0) for cid in seeds[idx]))


def _cluster_packages(cid: int, cluster_result: ClusterResult) -> set[str]:
    """Directories of the files a leaf cluster touches (its 'package')."""
    return {os.path.dirname(path) for path in cluster_result.cluster_to_files.get(cid, set())}


def supercluster_by_modularity_peak(
    cluster_result: ClusterResult,
    cfg_graph: nx.DiGraph,
    low: int = TOP_LEVEL_COMPONENTS_MIN,
    high: int = TOP_LEVEL_COMPONENTS_MAX,
    seed: int = ClusteringConfig.CLUSTERING_SEED,
) -> list[set[int]]:
    """Group leaf clusters into N top-level components via resolution-tuned Leiden.

    Resolution tuning steers Leiden over the weighted inter-cluster meta-graph;
    N (the number of top-level components) is the modularity peak among
    partitions with ``[low, high]`` non-singleton communities. Those communities
    become seeds and every remaining leaf cluster is absorbed into the nearest
    seed (call proximity, then package, then size). Returns the partition as a
    list of leaf-cluster-id sets — a complete, disjoint cover.

    Unlike ``merge_clusters`` (which targets a preset count and absorbs down to
    it by size), N here is data-driven: the sweep + modularity peak pick both the
    partition and its size, and absorption is seed-directed rather than
    smallest-first.
    """
    meta_graph = _build_meta_graph(cluster_result, cfg_graph)
    n_leaf = meta_graph.number_of_nodes()
    if n_leaf == 0:
        return []
    if n_leaf <= low:
        # Fewer leaf clusters than the floor — each is its own component.
        return [{cid} for cid in meta_graph.nodes]

    method_count = {cid: len(members) for cid, members in cluster_result.clusters.items()}
    communities = _pick_peak_partition(meta_graph, low, min(high, n_leaf), seed)
    seeds, leftovers = _seeds_from_partition(communities, method_count, low, min(high, n_leaf))

    if seeds:
        undirected_meta = meta_graph.to_undirected(as_view=True) if meta_graph.is_directed() else meta_graph
        seed_packages = [{pkg for cid in seed for pkg in _cluster_packages(cid, cluster_result)} for seed in seeds]
        for cid in leftovers:
            idx = _nearest_seed_index(cid, seeds, undirected_meta, seed_packages, cluster_result, method_count)
            seeds[idx].add(cid)
            seed_packages[idx].update(_cluster_packages(cid, cluster_result))

    logger.info(
        f"[SuperCluster] {n_leaf} leaf clusters -> {len(seeds)} components "
        f"(sizes {sorted((len(s) for s in seeds), reverse=True)})"
    )
    return seeds


def supercluster_leaf_ids(
    cluster_results: dict[str, ClusterResult],
    cfg_graphs: dict[str, nx.DiGraph],
    low: int = TOP_LEVEL_COMPONENTS_MIN,
    high: int = TOP_LEVEL_COMPONENTS_MAX,
    seed: int = ClusteringConfig.CLUSTERING_SEED,
) -> list[set[int]]:
    """Partition all languages' leaf clusters into N top-level component groups.

    Builds one combined meta-graph across languages (leaf-cluster IDs are already
    globally unique) so the returned groups sum to a single top-level count in
    ``[low, high]``. Each group is a set of leaf-cluster IDs; the LLM later only
    names and describes these fixed groups.
    """
    combined = _combine_cluster_results(cluster_results)
    combined_cfg: nx.DiGraph = nx.compose_all(list(cfg_graphs.values())) if cfg_graphs else nx.DiGraph()
    return supercluster_by_modularity_peak(combined, combined_cfg, low, high, seed)


def subgraph_peak_modularity(
    cluster_results: dict[str, ClusterResult],
    cfg_graphs: dict[str, nx.DiGraph],
    low: int = SUBCOMPONENTS_MIN,
    high: int = SUBCOMPONENTS_MAX,
    seed: int = ClusteringConfig.CLUSTERING_SEED,
) -> float:
    """Modularity of the sub-component split we would produce for this component — its separability.

    This is the Newman modularity of the exact ``[low, high]`` partition
    ``supercluster_by_modularity_peak`` would build (via ``_pick_peak_partition``),
    so the score is the quality of the real split, not an abstract graph metric. A
    high value means the internals separate cleanly (worth expanding); near-zero
    means a cohesive blob (a leaf). Returns 0.0 when there are fewer than two leaf
    clusters or no inter-cluster edges (``nx`` modularity is undefined on an
    edgeless graph, and a single cluster cannot split).
    """
    combined = _combine_cluster_results(cluster_results)
    n_clusters = len(combined.clusters)
    if n_clusters < 2:
        return 0.0
    combined_cfg: nx.DiGraph = nx.compose_all(list(cfg_graphs.values())) if cfg_graphs else nx.DiGraph()
    meta_graph = _build_meta_graph(combined, combined_cfg)
    if meta_graph.number_of_edges() == 0:
        return 0.0
    communities = _pick_peak_partition(meta_graph, low, min(high, n_clusters), seed)
    return nx_comm.modularity(meta_graph, communities, weight="weight")


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
