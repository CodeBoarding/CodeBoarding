"""A join becomes an edge the diagram can draw: two endpoints, a site, and a kind.

A unit's endpoint is a node for its own manifest (§4): the file a reader opens to find out what the
unit is. A shared file — a root compose file, an Aspire AppHost — is the edge's *site* rather than
an endpoint, so it belongs to no box and still says where the arrow was declared.

Placement is a second step, after the clustering, because what owns a file is a component and
components do not exist until the clustering has run. Wiring never moves a box: an endpoint joins
the group that already owns its directory, and no group's callables change.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from repo_utils.path_utils import normalize_repo_path
from static_analyzer.cfg import CallGraph, ReferenceEdge
from static_analyzer.clustering.models import ClusterScopeResult
from static_analyzer.config import NodeType
from static_analyzer.node import Node
from static_analyzer.wiring.join import Join
from static_analyzer.wiring_results import Unit


def emit(joins: list[Join], units: list[Unit], graphs: Mapping[str, CallGraph], repo_dir: Path) -> list[ReferenceEdge]:
    """One reference edge per joined pair and kind, between the endpoint nodes of the two units."""
    endpoints = endpoint_nodes(units, graphs, repo_dir)
    sites: dict[tuple[str, str, object], list[dict]] = {}
    for found in joins:
        source, target = endpoints.get(found.source), endpoints.get(found.target)
        if source is None or target is None or source.fully_qualified_name == target.fully_qualified_name:
            continue
        key = (source.fully_qualified_name, target.fully_qualified_name, found.kind)
        site = {"file": found.file, "line": max(found.line, 1), "column": max(found.column, 1)}
        if site not in sites.setdefault(key, []):
            sites[key].append(site)
    return [
        ReferenceEdge(str(source), str(target), kind, tuple(sites[(source, target, kind)]))  # type: ignore[arg-type]
        for source, target, kind in sorted(sites, key=lambda key: (str(key[0]), str(key[1]), str(key[2])))
    ]


def endpoint_nodes(units: list[Unit], graphs: Mapping[str, CallGraph], repo_dir: Path) -> dict[str, Node]:
    """The node an arrow lands on for each unit: its manifest, keyed by its repository path.

    Three units get none: one with no manifest has nothing to point at, one whose directory holds
    no analysed code is a resource rather than a box, and one whose directory is the repository
    itself names the whole tree — a root manifest is not a box, and an arrow into it would land on
    whichever component happens to hold the most code.
    """
    return {
        unit.id: Node(unit.manifest, NodeType.FILE, str(repo_dir / unit.manifest), 1, 1)
        for unit in units
        if unit.manifest and unit.dir != "." and languages_of(unit, graphs, repo_dir)
    }


def languages_of(unit: Unit, graphs: Mapping[str, CallGraph], repo_dir: Path) -> dict[str, int]:
    """How many analysed symbols each language has inside this unit, which is what makes it code.

    Why the repository directory: a graph's paths are absolute and a unit's are not, so the two
    only compare once both are spelled from the root.
    """
    prefix = "" if unit.dir == "." else f"{unit.dir}/"
    counted = {}
    for language, graph in graphs.items():
        inside = sum(
            1 for node in graph.nodes.values() if normalize_repo_path(node.file_path, repo_dir).startswith(prefix)
        )
        if inside:
            counted[language] = inside
    return counted


def place(hierarchy: ClusterScopeResult, units: list[Unit], edges: list[ReferenceEdge], repo_dir: Path) -> None:
    """Give every endpoint a node, a graph and the group that already owns its directory.

    Why here and not in the pass: a component is the clustering's answer, and an arrow can only
    land on a box once the boxes exist.
    """
    if not edges:
        return
    endpoints = endpoint_nodes(units, hierarchy.graphs_by_language, repo_dir)
    owners = {unit.id: unit for unit in units}
    for unit_id, node in sorted(endpoints.items()):
        language = _language(owners[unit_id], hierarchy.graphs_by_language, repo_dir)
        if not language:
            continue
        graph = hierarchy.graphs_by_language[language]
        if node.fully_qualified_name not in graph.nodes:
            graph.add_node(node)
        _own(hierarchy, owners[unit_id], language, node.fully_qualified_name, repo_dir)
    for edge in edges:
        language = _language_of_node(edge.src, endpoints, owners, hierarchy.graphs_by_language, repo_dir)
        graph = hierarchy.graphs_by_language.get(language) if language else None
        if graph is not None and edge.src in graph.nodes and edge.dst in graph.nodes:
            graph.add_reference_edge(edge)


def _own(scope: ClusterScopeResult, unit: Unit, language: str, name: str, repo_dir: Path) -> None:
    """Add the node to the group that owns most of this unit's code, in this scope and below it."""
    group = _owning_group(scope, unit, language, repo_dir)
    if group is None:
        return
    group.symbol_members_by_language.setdefault(language, set()).add(name)
    if group.children is not None:
        _own(group.children, unit, language, name, repo_dir)


def _owning_group(scope: ClusterScopeResult, unit: Unit, language: str, repo_dir: Path):
    """The group of this scope holding most of the unit's symbols, or none when no group does."""
    prefix = "" if unit.dir == "." else f"{unit.dir}/"
    graph = scope.graphs_by_language.get(language)
    if graph is None:
        return None
    counted = {}
    for group in scope.groups:
        inside = sum(
            1
            for member in group.symbol_members_by_language.get(language, set())
            if member in graph.nodes and normalize_repo_path(graph.nodes[member].file_path, repo_dir).startswith(prefix)
        )
        if inside:
            counted[group.group_id] = inside
    if not counted:
        return None
    winner = max(sorted(counted), key=lambda group_id: counted[group_id])
    return next(group for group in scope.groups if group.group_id == winner)


def _language(unit: Unit, graphs: Mapping[str, CallGraph], repo_dir: Path) -> str:
    counted = languages_of(unit, graphs, repo_dir)
    return max(sorted(counted), key=lambda language: counted[language]) if counted else ""


def _language_of_node(
    name: str,
    endpoints: Mapping[str, Node],
    owners: Mapping[str, Unit],
    graphs: Mapping[str, CallGraph],
    repo_dir: Path,
) -> str:
    for unit_id, node in endpoints.items():
        if node.fully_qualified_name == name:
            return _language(owners[unit_id], graphs, repo_dir)
    return ""
