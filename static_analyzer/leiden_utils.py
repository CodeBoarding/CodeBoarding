"""NetworkX <-> igraph conversion + leidenalg seeded-Leiden helpers.

Contains the ``igraph`` / ``leidenalg`` dependency surface so a future
engine swap stays localized. Downstream callers go through
``detect_communities`` in ``static_analyzer.graph``.
"""

from __future__ import annotations

import igraph as ig
import leidenalg as la
import networkx as nx


def nx_to_ig[T](graph: nx.Graph | nx.DiGraph) -> tuple[ig.Graph, list[T]]:
    """Convert a NetworkX graph to igraph; return (ig_graph, idx_to_qname).

    ``idx_to_qname[i]`` is the NetworkX node name for igraph vertex ``i``.
    Edge ``weight`` attributes are preserved as ``ig_graph.es["weight"]``.
    """
    ig_graph = ig.Graph.from_networkx(graph)
    idx_to_qname: list[T] = [v["_nx_name"] for v in ig_graph.vs]
    return ig_graph, idx_to_qname


def partition_to_clusters[T](
    partition: la.VertexPartition,
    idx_to_qname: list[T],
) -> list[set[T]]:
    """Group qnames by community id from a leidenalg partition."""
    clusters: dict[int, set[T]] = {}
    for v_idx, cid in enumerate(partition.membership):
        clusters.setdefault(cid, set()).add(idx_to_qname[v_idx])
    return list(clusters.values())


def find_partition[T](
    graph: nx.Graph | nx.DiGraph,
    *,
    weight: str | None = None,
    resolution: float | None = None,
    seed: int | None = None,
) -> list[set[T]]:
    """Run Leiden on the graph from singletons; return list of community sets.

    The ``static_analyzer.graph.detect_communities`` public entry point
    forwards here. Uses ``RBConfigurationVertexPartition`` when a resolution
    is supplied, otherwise ``ModularityVertexPartition``.
    """
    if graph.number_of_nodes() == 0:
        return []
    ig_graph = ig.Graph.from_networkx(graph)
    idx_to_qname: list[T] = [v["_nx_name"] for v in ig_graph.vs]
    partition_type = la.RBConfigurationVertexPartition if resolution is not None else la.ModularityVertexPartition
    kwargs: dict[str, object] = {}
    if seed is not None:
        kwargs["seed"] = seed
    if weight is not None:
        kwargs["weights"] = weight
    if resolution is not None:
        kwargs["resolution_parameter"] = resolution
    partition = la.find_partition(ig_graph, partition_type, **kwargs)
    return partition_to_clusters(partition, idx_to_qname)


def find_partition_seeded(
    graph: nx.Graph | nx.DiGraph,
    *,
    initial_membership_compact: list[int],
    is_membership_fixed: list[bool],
    weight: str | None = None,
    seed: int | None = None,
) -> list[int]:
    """Run Leiden seeded with a compact initial partition and a per-vertex lock mask.

    ``initial_membership_compact`` must align with igraph vertex order and
    contain values in ``[0, n_vertices)``; the caller owns the compact remap
    and the inverse overlap-matching afterwards. Uses the Optimiser API
    because ``is_membership_fixed`` isn't exposed on ``find_partition``.
    """
    n = graph.number_of_nodes()
    if n == 0:
        return []
    if len(initial_membership_compact) != n:
        raise ValueError(f"initial_membership_compact length {len(initial_membership_compact)} != n_vertices {n}")
    if len(is_membership_fixed) != n:
        raise ValueError(f"is_membership_fixed length {len(is_membership_fixed)} != n_vertices {n}")
    if initial_membership_compact and max(initial_membership_compact) >= n:
        raise ValueError(
            f"initial_membership_compact contains value >= n_vertices ({n}); "
            "leidenalg requires values in [0, n_vertices). Remap arbitrary prior "
            "cluster IDs to a compact 0..k-1 range first."
        )
    ig_graph = ig.Graph.from_networkx(graph)
    optimiser = la.Optimiser()
    if seed is not None:
        optimiser.set_rng_seed(seed)
    partition_kwargs: dict[str, object] = {"initial_membership": initial_membership_compact}
    if weight is not None:
        partition_kwargs["weights"] = weight
    partition = la.ModularityVertexPartition(ig_graph, **partition_kwargs)
    optimiser.optimise_partition(partition, is_membership_fixed=is_membership_fixed)
    return list(partition.membership)
