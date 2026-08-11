"""Deterministic 2-D layouts for the viewer, nested so every level stays visible.

Positions encode the hierarchy: a component's subtree is packed inside a circle,
its children inside smaller circles, down to the leaf clusters, so the viewer can
draw one point per method and still show which cluster, which sub-component and
which top-level component it belongs to. Connectivity decides where siblings sit
relative to each other — Fruchterman-Reingold over the graph *between* them —
so a cluster ends up next to the clusters it actually calls.

Contains this tool's ``igraph`` dependency surface, mirroring how
``static_analyzer.leiden_utils`` contains it for the analyzer.
"""

import math
import random
from dataclasses import dataclass, field

import igraph as ig
import numpy as np

_LAYOUT_SEED = 42
#: Radius given to a leaf bucket holding a single method, in layout units.
_MEMBER_RADIUS = 1.0
#: Gap kept between sibling circles, as a fraction of their radii.
_PADDING = 1.12


@dataclass
class Bucket:
    """One node of the layout tree: a component, a cluster, or the repo root."""

    key: str
    children: list["Bucket"] = field(default_factory=list)
    #: Graph node indices held directly by this bucket (leaf buckets only).
    members: list[int] = field(default_factory=list)

    def subtree_members(self) -> list[int]:
        if not self.children:
            return list(self.members)
        return [member for child in self.children for member in child.subtree_members()]


@dataclass
class Placement:
    """Absolute positions produced by ``layout_hierarchy``."""

    nodes: dict[int, tuple[float, float]]
    #: bucket key -> (x, y, radius), for drawing one circle per level.
    circles: dict[str, tuple[float, float, float]]


def _seeded_igraph() -> None:
    """Pin igraph's RNG so every layout is reproducible across runs."""
    ig.set_random_number_generator(random.Random(_LAYOUT_SEED))


def _phyllotaxis(count: int, radius: float) -> np.ndarray:
    """Evenly spread ``count`` points over a disc of ``radius`` (sunflower packing)."""
    if count == 1:
        return np.zeros((1, 2))
    indices = np.arange(count, dtype=float)
    r = radius * np.sqrt((indices + 0.5) / count)
    theta = indices * math.pi * (3.0 - math.sqrt(5.0))
    return np.stack([r * np.cos(theta), r * np.sin(theta)], axis=1)


def _sibling_positions(count: int, edges: dict[tuple[int, int], float], radii: list[float]) -> np.ndarray:
    """Place ``count`` sibling circles, pulling connected ones together."""
    if count == 1:
        return np.zeros((1, 2))

    spread = float(sum(radii)) * _PADDING
    if not edges:
        return _phyllotaxis(count, spread) if count > 2 else np.array([[-spread / 2, 0.0], [spread / 2, 0.0]][:count])

    graph = ig.Graph(n=count, edges=list(edges.keys()))
    _seeded_igraph()
    raw = np.asarray(graph.layout_fruchterman_reingold(weights=list(edges.values()), niter=800).coords, dtype=float)
    raw -= raw.mean(axis=0)
    extent = float(np.abs(raw).max())
    return raw * (spread / extent) if extent > 0 else _phyllotaxis(count, spread)


def _resolve_overlaps(positions: np.ndarray, radii: list[float], iterations: int = 400) -> np.ndarray:
    """Push overlapping sibling circles apart without unwinding the layout."""
    if len(radii) < 2:
        return positions
    radius = np.asarray(radii, dtype=float)
    for _ in range(iterations):
        delta = positions[:, None, :] - positions[None, :, :]
        distance = np.linalg.norm(delta, axis=2)
        np.fill_diagonal(distance, np.inf)
        wanted = (radius[:, None] + radius[None, :]) * _PADDING
        overlap = wanted - distance
        if float(overlap.max()) <= 1e-6:
            break
        direction = np.divide(delta, distance[:, :, None], out=np.zeros_like(delta), where=distance[:, :, None] > 0)
        # A circle with no room in any direction still needs to move: nudge it outward.
        stuck = distance == 0
        if bool(stuck.any()):
            angles = np.arange(len(radii), dtype=float) * 2.399
            direction[stuck] = np.stack([np.cos(angles), np.sin(angles)], axis=1)[np.where(stuck)[0]]
        push = np.where(overlap > 0, overlap, 0.0)[:, :, None] * direction * 0.5
        positions = positions + push.sum(axis=1)
    return positions


def layout_hierarchy(root: Bucket, edges: list[tuple[int, int, float]]) -> Placement:
    """Lay out every graph node inside its bucket, and every bucket inside its parent.

    ``edges`` are ``(source_index, target_index, weight)`` over the same node
    indices the buckets hold; they steer sibling placement at every level.
    """
    owner: dict[int, list[Bucket]] = {}

    def index_members(bucket: Bucket, ancestry: list[Bucket]) -> None:
        chain = [*ancestry, bucket]
        for member in bucket.members:
            owner[member] = chain
        for child in bucket.children:
            index_members(child, chain)

    index_members(root, [])

    local: dict[str, np.ndarray] = {}
    radius_of: dict[str, float] = {}

    def place(bucket: Bucket) -> float:
        if not bucket.children:
            count = max(len(bucket.members), 1)
            radius = _MEMBER_RADIUS * math.sqrt(count) * 1.6
            local[bucket.key] = _phyllotaxis(count, radius) if bucket.members else np.zeros((0, 2))
            radius_of[bucket.key] = radius + _MEMBER_RADIUS
            return radius_of[bucket.key]

        child_radii = [place(child) for child in bucket.children]
        by_member: dict[int, int] = {}
        for position, child in enumerate(bucket.children):
            for member in child.subtree_members():
                by_member[member] = position

        aggregated: dict[tuple[int, int], float] = {}
        for source, target, weight in edges:
            src_child, dst_child = by_member.get(source), by_member.get(target)
            if src_child is None or dst_child is None or src_child == dst_child:
                continue
            key = (min(src_child, dst_child), max(src_child, dst_child))
            aggregated[key] = aggregated.get(key, 0.0) + weight

        positions = _sibling_positions(len(bucket.children), aggregated, child_radii)
        positions = _resolve_overlaps(positions, child_radii)
        positions -= positions.mean(axis=0)
        local[bucket.key] = positions
        radius_of[bucket.key] = (
            float(max(np.linalg.norm(positions[i]) + child_radii[i] for i in range(len(child_radii)))) * 1.04
        )
        return radius_of[bucket.key]

    place(root)

    nodes: dict[int, tuple[float, float]] = {}
    circles: dict[str, tuple[float, float, float]] = {}

    def absolutize(bucket: Bucket, origin: np.ndarray) -> None:
        circles[bucket.key] = (float(origin[0]), float(origin[1]), radius_of[bucket.key])
        offsets = local[bucket.key]
        if not bucket.children:
            for position, member in enumerate(bucket.members):
                point = origin + offsets[position]
                nodes[member] = (float(point[0]), float(point[1]))
            return
        for position, child in enumerate(bucket.children):
            absolutize(child, origin + offsets[position])

    absolutize(root, np.zeros(2))
    return Placement(nodes=nodes, circles=circles)


def layout_graph[K](node_keys: list[K], edges: list[tuple[K, K, float]]) -> dict[K, tuple[float, float]]:
    """Plain force-directed layout, used for the per-scope meta-graph views."""
    if not node_keys:
        return {}
    if len(node_keys) == 1:
        return {node_keys[0]: (0.0, 0.0)}

    position_of = {key: position for position, key in enumerate(node_keys)}
    pairs: dict[tuple[int, int], float] = {}
    for source, target, weight in edges:
        if source not in position_of or target not in position_of or source == target:
            continue
        key = (position_of[source], position_of[target])
        pairs[key] = pairs.get(key, 0.0) + weight

    graph = ig.Graph(n=len(node_keys), edges=list(pairs.keys()))
    _seeded_igraph()
    if pairs:
        coords = np.asarray(graph.layout_fruchterman_reingold(weights=list(pairs.values()), niter=1000).coords)
    else:
        coords = _phyllotaxis(len(node_keys), float(len(node_keys)) ** 0.5)
    coords = np.asarray(coords, dtype=float)
    coords -= coords.mean(axis=0)
    extent = float(np.abs(coords).max())
    if extent > 0:
        coords /= extent
    return {key: (float(coords[position][0]), float(coords[position][1])) for key, position in position_of.items()}
