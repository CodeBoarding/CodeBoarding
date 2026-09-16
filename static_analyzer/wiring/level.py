"""Where a resource node is drawn, and what happens when a level would show more than the cap (§7).

A resource is a peer where its users meet: it is drawn inside its home, at the level below it, or at
the top when its home is the top. A home that is a box with no children at this depth cannot hold a
node, so the resource is a *badge* on that box, shown when the box expands. A level draws at most
``LIMIT`` nodes, code boxes and resource nodes alike; over the cap the resources fold, in order:
registry, configuration and telemetry resources into one Infrastructure node, other third parties
into one External services node and, when resources still take more than half the level, shared
data stores into one Data stores node. Private resources fold first in the page's order and are
badges already by construction: a resource with one user sits in that user's deepest box, which
holds no children at the level where the count is made. What is still over the cap after the folds
is the clustering's to settle, and this module only reports it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from static_analyzer.clustering.names.draft import LIMIT
from static_analyzer.wiring.emit import Placement
from static_analyzer.wiring_results import Resource, ResourceKind

INFRASTRUCTURE = "Infrastructure"
EXTERNAL = "External services"
STORES = "Data stores"
_STORE_KINDS = frozenset({ResourceKind.DB, ResourceKind.CACHE, ResourceKind.STORE})


@dataclass
class Layout:
    """Every resource placed, and per parent (empty for the top) how many nodes its level draws:
    code boxes, resource nodes after the folds, and how many over the cap that still is."""

    resources: list[Resource] = field(default_factory=list)
    levels: dict[str, tuple[int, int, int]] = field(default_factory=dict)


def layout(resources: Sequence[Resource], placement: Placement, children: Mapping[str, Sequence[str]]) -> Layout:
    """``children`` maps each expanded component id, and the empty id for the top, to its children."""
    live = {child for held in children.values() for child in held} | {parent for parent in children if parent}
    infrastructure = _infrastructure(placement)
    placed = []
    for resource in resources:
        home = _lift(placement.homes.get(resource.key, ""), live)
        badge = bool(home) and not children.get(home)
        placed.append(
            replace(
                resource,
                home=home,
                level=_depth(home) + 1,
                badge=badge,
                children=tuple(
                    replace(child, home=_lift(placement.homes.get(child.key, home), live))
                    for child in resource.children
                ),
            )
        )
    found = Layout()
    by_parent: dict[str, list[int]] = {}
    for index, resource in enumerate(placed):
        if not resource.badge:
            by_parent.setdefault(resource.home, []).append(index)
    for parent in sorted(set(children) | set(by_parent)):
        boxes = len(children.get(parent, ()))
        drawn = list(by_parent.get(parent, []))
        groups: dict[str, list[int]] = {}

        def fold(name: str, chosen: list[int]) -> None:
            if len(chosen) < 2:
                return
            groups[name] = chosen
            for index in chosen:
                placed[index] = replace(placed[index], group=name)
                drawn.remove(index)

        if boxes + len(drawn) > LIMIT:
            fold(INFRASTRUCTURE, [index for index in drawn if placed[index].key in infrastructure])
        if boxes + len(drawn) + len(groups) > LIMIT:
            fold(EXTERNAL, [index for index in drawn if placed[index].kind is ResourceKind.API])
        if boxes + len(drawn) + len(groups) > LIMIT and 2 * (len(drawn) + len(groups)) > boxes + len(drawn) + len(
            groups
        ):
            fold(STORES, [index for index in drawn if placed[index].kind in _STORE_KINDS])
        nodes = len(drawn) + len(groups)
        found.levels[parent] = (boxes, nodes, max(0, boxes + nodes - LIMIT))
    found.resources = placed
    return found


def _infrastructure(placement: Placement) -> set[str]:
    """The resources every edge of which is registering, fetching configuration or reporting (§5)."""
    kinds: dict[str, set[bool]] = {}
    for edge in placement.graph.reference_edges:
        for end in (edge.src, edge.dst):
            if end in placement.homes:
                kinds.setdefault(end, set()).add(edge.kind.infrastructure)
    return {key for key, flags in kinds.items() if flags == {True}}


def _lift(component_id: str, live: set[str]) -> str:
    """The deepest ancestor of a component the document holds, so a home is never a box it does not draw."""
    parts = component_id.split(".") if component_id else []
    for length in range(len(parts), 0, -1):
        candidate = ".".join(parts[:length])
        if candidate in live:
            return candidate
    return ""


def _depth(component_id: str) -> int:
    return component_id.count(".") + 1 if component_id else 0
