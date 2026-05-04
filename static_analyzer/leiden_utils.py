"""NetworkX <-> igraph conversion + leidenalg seeded-Leiden helpers.

Why a separate module: keeps the new dependency surface (`igraph`, `leidenalg`)
contained and makes a future engine swap localized. ``static_analyzer.graph``
imports from here; downstream callers go through ``detect_communities``.

Key API quirks discovered during Phase 0 verification:
- ``initial_membership`` values must satisfy ``max(v) < n_vertices``;
  arbitrary prior cluster IDs need to be remapped to a compact 0..k-1 range
  and reconstructed afterward by overlap matching (see ``cluster_delta``).
- ``is_membership_fixed`` lives on ``Optimiser.optimise_partition``, not on
  ``find_partition``. The seeded path here uses the Optimiser API directly.
"""

from __future__ import annotations

import igraph as ig
import leidenalg as la
import networkx as nx


def nx_to_ig[T](graph: nx.Graph | nx.DiGraph) -> tuple[ig.Graph, list[T]]:
    """Convert a NetworkX graph to igraph; return (ig_graph, idx_to_qname).

    ``idx_to_qname[i]`` gives the original NetworkX node name for igraph
    vertex index ``i``. ``ig.Graph.from_networkx`` preserves edge attribute
    ``weight`` as ``ig_graph.es["weight"]`` when present on every edge.
    Generic over node name type so callers using string qnames or int
    cluster IDs both type-check cleanly.
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

    Wraps ``leidenalg.find_partition``. ``RBConfigurationVertexPartition`` is
    used when a resolution is supplied (otherwise ``ModularityVertexPartition``).
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

    ``initial_membership_compact`` must be aligned with igraph vertex order
    and contain values in ``[0, n_vertices)``. Caller is responsible for the
    compact remap (and for un-mapping output IDs back to prior IDs by overlap).

    Why the Optimiser API: ``is_membership_fixed`` is not exposed on
    ``leidenalg.find_partition``; only ``Optimiser.optimise_partition`` accepts it.
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
