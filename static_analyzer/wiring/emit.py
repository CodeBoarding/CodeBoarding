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
    """The node an arrow lands on for each unit: its manifest, keyed by its repository path."""
    return _endpoints(units, languages_by_unit(units, graphs, repo_dir), repo_dir)


def languages_by_unit(units: list[Unit], graphs: Mapping[str, CallGraph], repo_dir: Path) -> dict[str, dict[str, int]]:
    """How many analysed symbols each language has inside each unit, from one walk of the graphs.

    Why one walk and not one question per unit: a symbol belongs to every unit whose directory
    contains it, so asking each unit in turn rescans every graph once per unit. On a repository of
    twenty units that is twenty full passes to answer one question, and it was the dominant cost of
    the whole layer — eShop spent 6.8 % of its static phase here, against a 5 % budget for the pass.

    Why the repository directory: a graph's paths are absolute and a unit's are not, so the two only
    compare once both are spelled from the root.
    """
    directories = {unit.dir for unit in units if unit.dir != "."}
    counted: dict[str, dict[str, int]] = {}
    for language, graph in graphs.items():
        for node in graph.nodes.values():
            segments = normalize_repo_path(node.file_path, repo_dir).split("/")
            for depth in range(1, len(segments)):
                ancestor = "/".join(segments[:depth])
                if ancestor in directories:
                    languages = counted.setdefault(ancestor, {})
                    languages[language] = languages.get(language, 0) + 1
    return counted


def place(hierarchy: ClusterScopeResult, units: list[Unit], edges: list[ReferenceEdge], repo_dir: Path) -> None:
    """Give every endpoint a node, a graph and the group that already owns its directory.

    Why here and not in the pass: a component is the clustering's answer, and an arrow can only
    land on a box once the boxes exist.
    """
    if not edges:
        return
    graphs = hierarchy.graphs_by_language
    inside = languages_by_unit(units, graphs, repo_dir)
    endpoints = _endpoints(units, inside, repo_dir)
    owners = {unit.id: unit for unit in units}
    paths: dict[str, str] = {}
    language_of: dict[str, str] = {}
    node_of: dict[str, Node] = {}
    for unit_id, node in sorted(endpoints.items()):
        counted = inside.get(owners[unit_id].dir, {})
        language = max(sorted(counted), key=lambda one: counted[one])
        graph = graphs[language]
        if node.fully_qualified_name not in graph.nodes:
            graph.add_node(node)
        _own(hierarchy, owners[unit_id], language, node.fully_qualified_name, repo_dir, paths)
        language_of[node.fully_qualified_name] = language
        node_of[node.fully_qualified_name] = node
    for edge in edges:
        graph = graphs.get(language_of.get(edge.src, ""))
        if graph is None or edge.src not in graph.nodes:
            continue
        # An arrow whose two ends are written in different languages is the case this layer exists
        # for — a compose file wiring a Python service to a Java one — and an edge can only be drawn
        # in a graph holding both its ends, so the target's node joins the source's graph. Dropping
        # it instead lost every cross-language arrow on three of the seven rulers, silently.
        if edge.dst not in graph.nodes and edge.dst in node_of:
            graph.add_node(node_of[edge.dst])
        if edge.dst in graph.nodes:
            graph.add_reference_edge(edge)


def _endpoints(units: list[Unit], inside: dict[str, dict[str, int]], repo_dir: Path) -> dict[str, Node]:
    """Three units get no endpoint: one with no manifest has nothing to point at, one whose
    directory holds no analysed code is a resource rather than a box, and one whose directory is the
    repository itself names the whole tree — an arrow into it would land on whichever component
    happens to hold the most code.
    """
    return {
        unit.id: Node(unit.manifest, NodeType.FILE, str(repo_dir / unit.manifest), 1, 1)
        for unit in units
        if unit.manifest and unit.dir != "." and inside.get(unit.dir)
    }


def _own(
    scope: ClusterScopeResult, unit: Unit, language: str, name: str, repo_dir: Path, paths: dict[str, str]
) -> None:
    """Add the node to the group that owns most of this unit's code, in this scope and below it."""
    group = _owning_group(scope, unit, language, repo_dir, paths)
    if group is None:
        return
    group.symbol_members_by_language.setdefault(language, set()).add(name)
    if group.children is not None:
        _own(group.children, unit, language, name, repo_dir, paths)


def _owning_group(scope: ClusterScopeResult, unit: Unit, language: str, repo_dir: Path, paths: dict[str, str]):
    """The group of this scope holding most of the unit's symbols, or none when no group does."""
    prefix = f"{unit.dir}/"
    graph = scope.graphs_by_language.get(language)
    if graph is None:
        return None
    counted = {}
    for group in scope.groups:
        inside = 0
        for member in group.symbol_members_by_language.get(language, set()):
            node = graph.nodes.get(member)
            if node is None:
                continue
            path = paths.get(node.file_path) or paths.setdefault(
                node.file_path, normalize_repo_path(node.file_path, repo_dir)
            )
            inside += path.startswith(prefix)
        if inside:
            counted[group.group_id] = inside
    if not counted:
        return None
    winner = max(sorted(counted), key=lambda group_id: counted[group_id])
    return next(group for group in scope.groups if group.group_id == winner)
