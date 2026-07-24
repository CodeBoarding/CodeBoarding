"""Group a language's leaf clusters into the architecture's top-level components.

Two stages, both deterministic:

1. **Partition** — resolution-tuned Leiden over a weighted meta-graph of
   inter-cluster call edges picks both the component count (the modularity peak
   over ``[low, high]``) and the membership. The LLM only names the result.
2. **Absorption** — real call graphs carry a long tail of leaf clusters with no
   inter-cluster edge at all, which Leiden leaves as singletons. Each is folded
   into the nearest seed by call proximity, then by directory affinity, with the
   smaller seed winning ties so the tail spreads instead of piling onto one
   component.
"""

import logging
import os
from collections import defaultdict, deque

import networkx as nx
import networkx.algorithms.community as nx_comm

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import ClusteringConfig, Language
from static_analyzer.graph import ClusterResult, detect_communities

logger = logging.getLogger(__name__)

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


def build_all_cluster_results(static_analysis: StaticAnalysisResults) -> dict[str, ClusterResult]:
    """Cluster every detected language and give the clusters a shared ID namespace.

    Downstream code maps ``cluster_id -> component`` in a single dict, so IDs must
    not collide across languages.
    """
    cluster_results: dict[str, ClusterResult] = {}
    offset = 0
    for lang in static_analysis.get_languages():
        result = static_analysis.get_cfg(lang).cluster()
        if offset:
            result = reindex_cluster_result(result, offset)
            logger.info(f"[Cluster] {lang}: offset IDs by +{offset} ({len(result.clusters)} clusters)")
        cluster_results[str(lang)] = result
        offset += max(result.clusters, default=0) + 1

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


def reindex_across_languages(cluster_results: dict[str, ClusterResult]) -> None:
    """Give each language's clusters a disjoint ID range, in place.

    Needed wherever per-language ``ClusterResult``s are built independently (the
    per-component subgraphs) and then merged into one ``cluster_id -> component``
    lookup.
    """
    if len(cluster_results) <= 1:
        return
    offset = 0
    for lang in sorted(cluster_results):
        result = cluster_results[lang]
        if offset:
            cluster_results[lang] = reindex_cluster_result(result, offset)
            logger.info(f"[ReIndex] {lang}: offset IDs by +{offset} (now {offset + 1}-{offset + len(result.clusters)})")
        offset += max(result.clusters, default=0) + 1


def reindex_cluster_result(cluster_result: ClusterResult, offset: int) -> ClusterResult:
    """Return a copy of *cluster_result* with every cluster ID shifted by *offset*."""
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


# ---------------------------------------------------------------------------
# Meta-graph construction
# ---------------------------------------------------------------------------


def _build_meta_graph(cluster_result: ClusterResult, cfg_graph: nx.DiGraph) -> nx.DiGraph:
    """Build a weighted directed meta-graph of inter-cluster connectivity.

    Each node is a cluster ID. Each edge ``(src_cid, dst_cid)`` carries the
    number of CFG calls from ``src_cid`` members into ``dst_cid`` members.
    Mutual coupling A<->B becomes two separate edges, each contributing
    independently to directed Leicht-Newman modularity.
    """
    node_to_cluster: dict[str, int] = {}
    for cluster_id, nodes in cluster_result.clusters.items():
        for node in nodes:
            node_to_cluster[node] = cluster_id

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


def combine_cluster_results(cluster_results: dict[str, ClusterResult]) -> ClusterResult:
    """Union per-language ClusterResults into one.

    Cluster IDs are globally unique across languages, so a plain union is safe and
    lets us group every language's leaf clusters against a single meta-graph.
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


# ---------------------------------------------------------------------------
# Partitioning
# ---------------------------------------------------------------------------


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

    # Biggest clusters first (they anchor packages), tie-broken by id, so the
    # order absorption sees never depends on set iteration order.
    leftovers.sort(key=lambda cid: (-method_count.get(cid, 0), cid))

    seeds = reals
    while len(seeds) < low and leftovers:
        seeds.append({leftovers.pop(0)})

    return seeds, leftovers


def _cluster_packages(cid: int, cluster_result: ClusterResult) -> set[str]:
    """Directories of the files a leaf cluster touches (its 'package')."""
    return {os.path.dirname(path) for path in cluster_result.cluster_to_files.get(cid, set())}


def _package_affinity(package: str, candidates: set[str]) -> int:
    """Leading path segments *package* shares with its closest match in *candidates*."""
    own = package.split(os.sep)
    best = 0
    for candidate in candidates:
        other = candidate.split(os.sep)
        shared = 0
        for a, b in zip(own, other):
            if a != b:
                break
            shared += 1
        best = max(best, shared)
    return best


def _seed_distances(meta_graph: nx.DiGraph, seeds: list[set[int]]) -> list[dict[int, int]]:
    """Hop distance from each seed to every leaf cluster it can reach.

    One multi-source BFS per seed on an undirected view — absorption is about
    topological proximity, not directional reachability, and a tiny utility
    cluster should be absorbable regardless of which way the calls flow.
    """
    undirected = meta_graph.to_undirected(as_view=True) if meta_graph.is_directed() else meta_graph
    distances: list[dict[int, int]] = []
    for seed in seeds:
        reached = {cid: 0 for cid in seed if undirected.has_node(cid)}
        frontier = deque(reached)
        while frontier:
            cid = frontier.popleft()
            for neighbour in undirected.neighbors(cid):
                if neighbour not in reached:
                    reached[neighbour] = reached[cid] + 1
                    frontier.append(neighbour)
        distances.append(reached)
    return distances


def _absorb_leftovers(
    seeds: list[set[int]],
    leftovers: list[int],
    meta_graph: nx.DiGraph,
    cluster_result: ClusterResult,
    method_count: dict[int, int],
) -> None:
    """Fold every leftover leaf cluster into a seed, in place.

    Preference order per leftover: fewest hops to a seed member, then deepest
    shared directory prefix with the seed's *original* packages, then the
    smallest seed. Ties always go to the smaller seed, and affinity is measured
    against the seed's pre-absorption packages — otherwise a seed that absorbed
    early keeps widening its package set and wins every later comparison, which
    is what collapses a repo into one mega-component.
    """
    distances = _seed_distances(meta_graph, seeds)
    seed_packages = [{pkg for cid in seed for pkg in _cluster_packages(cid, cluster_result)} for seed in seeds]
    sizes = [sum(method_count.get(cid, 0) for cid in seed) for seed in seeds]

    for cid in leftovers:
        ranked = [(reached[cid], sizes[idx], idx) for idx, reached in enumerate(distances) if cid in reached]
        if not ranked:
            packages = _cluster_packages(cid, cluster_result)
            for idx, seed_pkgs in enumerate(seed_packages):
                affinity = max((_package_affinity(pkg, seed_pkgs) for pkg in packages), default=0)
                if affinity:
                    ranked.append((-affinity, sizes[idx], idx))
        if not ranked:
            ranked = [(0, sizes[idx], idx) for idx in range(len(seeds))]
        target = min(ranked)[2]
        seeds[target].add(cid)
        sizes[target] += method_count.get(cid, 0)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def supercluster_by_modularity_peak(
    cluster_result: ClusterResult,
    cfg_graph: nx.DiGraph,
    low: int = TOP_LEVEL_COMPONENTS_MIN,
    high: int = TOP_LEVEL_COMPONENTS_MAX,
    seed: int = ClusteringConfig.CLUSTERING_SEED,
) -> tuple[list[set[int]], float]:
    """Group leaf clusters into N components; return the groups and the split's modularity.

    Resolution tuning steers Leiden over the weighted inter-cluster meta-graph;
    N (the number of components) is the modularity peak among partitions with
    ``[low, high]`` non-singleton communities. Those communities become seeds and
    every remaining leaf cluster is absorbed into the nearest one. The groups are
    a complete, disjoint cover of the leaf clusters.

    The returned modularity scores the partition the sweep chose, so a caller
    deciding *whether* to split and a caller performing the split read the same
    number. It is 0.0 when the meta-graph has no edges — nothing to separate.
    """
    meta_graph = _build_meta_graph(cluster_result, cfg_graph)
    n_leaf = meta_graph.number_of_nodes()
    if n_leaf == 0:
        return [], 0.0
    if n_leaf <= low:
        # Fewer leaf clusters than the floor — each is its own component.
        return [{cid} for cid in meta_graph.nodes], 0.0

    high = min(high, n_leaf)
    method_count = {cid: len(members) for cid, members in cluster_result.clusters.items()}
    communities = _pick_peak_partition(meta_graph, low, high, seed)
    modularity = nx_comm.modularity(meta_graph, communities, weight="weight") if meta_graph.number_of_edges() else 0.0
    seeds, leftovers = _seeds_from_partition(communities, method_count, low, high)
    if seeds:
        _absorb_leftovers(seeds, leftovers, meta_graph, cluster_result, method_count)

    logger.info(
        f"[SuperCluster] {n_leaf} leaf clusters -> {len(seeds)} components "
        f"(modularity={modularity:.4f}, sizes {sorted((len(s) for s in seeds), reverse=True)})"
    )
    return seeds, modularity


def supercluster_leaf_ids(
    cluster_results: dict[str, ClusterResult],
    cfg_graphs: dict[str, nx.DiGraph],
    low: int = TOP_LEVEL_COMPONENTS_MIN,
    high: int = TOP_LEVEL_COMPONENTS_MAX,
    seed: int = ClusteringConfig.CLUSTERING_SEED,
) -> tuple[list[set[int]], float]:
    """Partition all languages' leaf clusters into component groups, with the split's modularity.

    Builds one combined meta-graph across languages (leaf-cluster IDs are already
    globally unique) so the returned groups sum to a single top-level count in
    ``[low, high]``. Each group is a set of leaf-cluster IDs; the LLM later only
    names and describes these fixed groups.
    """
    combined = combine_cluster_results(cluster_results)
    combined_cfg: nx.DiGraph = nx.compose_all(list(cfg_graphs.values())) if cfg_graphs else nx.DiGraph()
    return supercluster_by_modularity_peak(combined, combined_cfg, low, high, seed)
