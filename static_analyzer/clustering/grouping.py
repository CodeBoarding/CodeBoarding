"""Group leaf clusters into deterministic architectural components."""

import logging
import os
from collections import Counter, defaultdict, deque

import networkx as nx
import networkx.algorithms.community as nx_comm

from static_analyzer.config import (
    DEFAULT_GROUPING_CONFIG,
    SUBCOMPONENT_GROUPING_CONFIG,
    GroupingConfig,
)
from static_analyzer.clustering.models import AnchoredGrouping, ClusterResult
from static_analyzer.leiden_utils import find_partition

logger = logging.getLogger(__name__)


class GroupingService:
    """Group leaf clusters without retaining graph or partition state."""

    def group(
        self,
        cluster_results: dict[str, ClusterResult],
        cfg_graphs: dict[str, nx.DiGraph],
        *,
        subcomponents: bool = False,
    ) -> tuple[list[set[int]], float]:
        """Group all languages' leaf clusters in one shared namespace."""
        config = SUBCOMPONENT_GROUPING_CONFIG if subcomponents else DEFAULT_GROUPING_CONFIG
        combined = combine_cluster_results(cluster_results)
        combined_cfg: nx.DiGraph = nx.compose_all(list(cfg_graphs.values())) if cfg_graphs else nx.DiGraph()
        return _group_by_modularity_peak(combined, combined_cfg, config)

    def anchored_group(
        self,
        cluster_results: dict[str, ClusterResult],
        cfg_graphs: dict[str, nx.DiGraph],
        previous_owner: dict[int, str],
        *,
        subcomponents: bool = False,
    ) -> AnchoredGrouping:
        """Carry previous ownership forward and regroup beyond the drift budget."""
        config = SUBCOMPONENT_GROUPING_CONFIG if subcomponents else DEFAULT_GROUPING_CONFIG
        combined = combine_cluster_results(cluster_results)
        combined_cfg: nx.DiGraph = nx.compose_all(list(cfg_graphs.values())) if cfg_graphs else nx.DiGraph()
        return _anchored_group(combined, combined_cfg, previous_owner, config)


def reindex_across_languages(cluster_results: dict[str, ClusterResult]) -> None:
    """Give each language's clusters a disjoint ID namespace, in place."""
    if len(cluster_results) <= 1:
        return
    # Preserve stable IDs from seeded incremental partitions that are already disjoint.
    id_sets = [set(result.clusters) for result in cluster_results.values()]
    if len(set().union(*id_sets)) == sum(len(cluster_ids) for cluster_ids in id_sets):
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


def group_symbols(cluster_ids: list[int], node_lookup: dict[int, set[str]]) -> list[str]:
    """Qualified names in a group, most top-level first (fewest name segments)."""
    names = {qname for cid in cluster_ids for qname in node_lookup.get(cid, set())}
    return sorted(names, key=lambda qname: (qname.count("."), qname))


def combine_cluster_results(cluster_results: dict[str, ClusterResult]) -> ClusterResult:
    """Union per-language cluster results with globally unique IDs."""
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


def _build_meta_graph(cluster_result: ClusterResult, cfg_graph: nx.DiGraph) -> nx.DiGraph:
    """Build a weighted directed graph of calls between clusters."""
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


def _pick_peak_partition(
    meta_graph: nx.DiGraph,
    config: GroupingConfig,
) -> list[set[int]]:
    """Return the resolution-sweep partition with peak in-range modularity.

    Why: isolated clusters should not inflate the target community count.
    """
    if meta_graph.number_of_edges() == 0:
        return [{cid} for cid in meta_graph.nodes]

    candidates: list[tuple[int, float, list[set[int]]]] = []
    for resolution in config.resolutions:
        try:
            communities: list[set[int]] = find_partition(
                meta_graph,
                weight="weight",
                resolution=resolution,
                seed=config.seed,
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

    in_range = [c for c in candidates if config.min_components <= c[0] <= config.max_components]
    if in_range:
        n_real, modularity, communities = max(in_range, key=lambda c: c[1])
        logger.info(
            f"[SuperCluster] modularity peak at N={n_real} (modularity={modularity:.4f}) "
            f"over [{config.min_components},{config.max_components}]"
        )
        return communities

    def range_distance(n_real: int) -> int:
        if config.min_components <= n_real <= config.max_components:
            return 0
        return min(abs(n_real - config.min_components), abs(n_real - config.max_components))

    n_real, _, communities = min(candidates, key=lambda c: (range_distance(c[0]), -c[1]))
    logger.info(
        f"[SuperCluster] no partition with N in [{config.min_components},{config.max_components}]; "
        f"using closest (N={n_real})"
    )
    return communities


def _seeds_from_partition(
    communities: list[set[int]],
    method_count: dict[int, int],
    config: GroupingConfig,
) -> tuple[list[set[int]], list[int]]:
    """Select seed communities and return the remaining leaf clusters."""
    reals = sorted(
        (set(c) for c in communities if len(c) >= 2),
        key=lambda community: (sum(method_count.get(cid, 0) for cid in community), -min(community)),
        reverse=True,
    )
    leftovers = [cid for c in communities if len(c) == 1 for cid in c]

    if len(reals) > config.max_components:
        leftovers.extend(cid for community in reals[config.max_components :] for cid in community)
        reals = reals[: config.max_components]

    # Stabilize absorption order while prioritizing the largest clusters.
    leftovers.sort(key=lambda cid: (-method_count.get(cid, 0), cid))

    seeds = reals
    while len(seeds) < config.min_components and leftovers:
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
        for a, b in zip(own, other, strict=False):
            if a != b:
                break
            shared += 1
        best = max(best, shared)
    return best


def _seed_distances(meta_graph: nx.DiGraph, seeds: list[set[int]]) -> list[dict[int, int]]:
    """Measure undirected hop distance from each seed to reachable clusters."""
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
    """Fold leftovers into seeds by distance, package affinity, then size."""
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


def _method_counts(cluster_result: ClusterResult) -> dict[int, int]:
    return {cid: len(members) for cid, members in cluster_result.clusters.items()}


def _modularity(meta_graph: nx.DiGraph, groups: list[set[int]]) -> float:
    """0.0 on an edgeless meta-graph — there is nothing to separate."""
    return nx_comm.modularity(meta_graph, groups, weight="weight") if meta_graph.number_of_edges() else 0.0


def _optimize_grouping(
    meta_graph: nx.DiGraph,
    cluster_result: ClusterResult,
    method_count: dict[int, int],
    config: GroupingConfig,
) -> tuple[list[set[int]], float]:
    """The from-scratch partition of a prebuilt meta-graph, with every leftover absorbed."""
    n_leaf = meta_graph.number_of_nodes()
    if n_leaf == 0:
        return [], 0.0
    if n_leaf <= config.min_components:
        # Fewer leaf clusters than the floor — each is its own component.
        return [{cid} for cid in meta_graph.nodes], 0.0

    communities = _pick_peak_partition(meta_graph, config)
    seeds, leftovers = _seeds_from_partition(communities, method_count, config)
    if seeds:
        _absorb_leftovers(seeds, leftovers, meta_graph, cluster_result, method_count)
    # Drift and expansion decisions require the score after absorption.
    return seeds, _modularity(meta_graph, seeds if seeds else communities)


def _group_by_modularity_peak(
    cluster_result: ClusterResult,
    cfg_graph: nx.DiGraph,
    config: GroupingConfig,
) -> tuple[list[set[int]], float]:
    """Group leaf clusters into components and return their modularity."""
    meta_graph = _build_meta_graph(cluster_result, cfg_graph)
    groups, modularity = _optimize_grouping(
        meta_graph,
        cluster_result,
        _method_counts(cluster_result),
        config,
    )
    logger.info(
        f"[SuperCluster] {meta_graph.number_of_nodes()} leaf clusters -> {len(groups)} components "
        f"(modularity={modularity:.4f}, sizes {sorted((len(g) for g in groups), reverse=True)})"
    )
    return groups, modularity


def _inherit_ids(
    groups: list[set[int]],
    previous_owner: dict[int, str],
    method_count: dict[int, int],
) -> list[str]:
    """Assign each group its dominant available previous component ID."""
    claims: list[tuple[int, Counter[str]]] = []
    for index, group in enumerate(groups):
        tally: Counter[str] = Counter()
        for cid in group:
            owner = previous_owner.get(cid)
            if owner:
                tally[owner] += method_count.get(cid, 0)
        claims.append((index, tally))

    owners = [""] * len(groups)
    taken: set[str] = set()
    for index, tally in sorted(claims, key=lambda claim: (-max(claim[1].values(), default=0), claim[0])):
        for owner, _weight in tally.most_common():
            if owner not in taken:
                taken.add(owner)
                owners[index] = owner
                break
    return owners


def _anchored_group(
    cluster_result: ClusterResult,
    cfg_graph: nx.DiGraph,
    previous_owner: dict[int, str],
    config: GroupingConfig,
) -> AnchoredGrouping:
    """Carry previous ownership forward and regroup beyond the drift budget."""
    meta_graph = _build_meta_graph(cluster_result, cfg_graph)
    live = set(meta_graph.nodes)
    if not live:
        return AnchoredGrouping([], [], False, 0.0, 0.0, 0)
    method_count = _method_counts(cluster_result)

    # Keep one stable group per surviving component.
    carried: dict[str, set[int]] = defaultdict(set)
    for cid in sorted(live):
        owner = previous_owner.get(cid)
        if owner:
            carried[owner].add(cid)
    if not carried:
        # A first run or unrelated baseline has no ownership to preserve.
        fresh, unanchored_modularity = _optimize_grouping(
            meta_graph,
            cluster_result,
            method_count,
            config,
        )
        return AnchoredGrouping(
            fresh,
            [""] * len(fresh),
            True,
            unanchored_modularity,
            unanchored_modularity,
            len(fresh),
        )

    owners = sorted(carried)
    groups = [carried[owner] for owner in owners]
    fresh_groups, unanchored_modularity = _optimize_grouping(
        meta_graph,
        cluster_result,
        method_count,
        config,
    )
    newcomers = set(live) - {cid for group in groups for cid in group}

    # Preserve new communities that have no previous owner as distinct subsystems.
    new_subsystems = [group for group in fresh_groups if len(group) >= 2 and group <= newcomers]
    for subsystem in new_subsystems:
        groups.append(set(subsystem))
        owners.append("")
    absorbed = sorted(newcomers - {cid for subsystem in new_subsystems for cid in subsystem})
    if absorbed:
        _absorb_leftovers(groups, absorbed, meta_graph, cluster_result, method_count)

    modularity = _modularity(meta_graph, groups)
    if unanchored_modularity - modularity > config.drift_budget:
        logger.info(
            f"[Anchored] carried grouping scores {modularity:.4f} vs {unanchored_modularity:.4f} unanchored "
            f"(> {config.drift_budget} budget); re-deriving structure from scratch"
        )
        return AnchoredGrouping(
            fresh_groups,
            _inherit_ids(fresh_groups, previous_owner, method_count),
            True,
            unanchored_modularity,
            unanchored_modularity,
            len(fresh_groups),
        )

    logger.info(
        f"[Anchored] {len(live)} leaf clusters -> {len(groups)} components carried forward "
        f"({len(new_subsystems)} new component(s), {len(absorbed)} clusters absorbed, "
        f"modularity={modularity:.4f} vs {unanchored_modularity:.4f} unanchored)"
    )
    return AnchoredGrouping(groups, owners, False, modularity, unanchored_modularity, len(fresh_groups))
