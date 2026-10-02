"""A join becomes an edge the diagram can draw: two endpoints, a site, and a kind.

A unit's endpoint is a node for its own manifest (§4): the file a reader opens to find out what the
unit is. Where the anchor is a literal in code, the endpoint is the enclosing code symbol instead. A
shared file — a root compose file, an Aspire AppHost — is the edge's *site* rather than an endpoint,
so it belongs to no box and still says where the arrow was declared.

Placement is a second step, after the clustering, because what owns a file is a component and
components do not exist until the clustering has run. Wiring never moves a box and never touches a
language graph: the drawn edges and their endpoint nodes go into one dedicated graph, rebuilt on
every run, and each endpoint is mapped to the component owning its unit's directory when the
relations are built.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from repo_utils.path_utils import normalize_repo_path
from static_analyzer.cfg import CallGraph, ReferenceEdge
from static_analyzer.clustering.models import ClusterScopeResult
from static_analyzer.config import CALLABLE_TYPES, CLASS_TYPES, NodeType
from static_analyzer.node import Node
from static_analyzer.wiring.join import Join
from static_analyzer.wiring.scan import SOURCE_SUFFIXES
from static_analyzer.wiring_results import Diagnostic, DiagnosticCode, Unit

#: The graph the wiring layer's edges live in. Never a language: nothing about it is code.
WIRING_GRAPH = "wiring"


@dataclass
class Placement:
    """The wiring graph and, for each artifact node in it, the component owning its unit's directory."""

    graph: CallGraph = field(default_factory=lambda: CallGraph(language=WIRING_GRAPH))
    owners: dict[str, str] = field(default_factory=dict)


def emit(
    joins: list[Join], units: list[Unit], graphs: Mapping[str, CallGraph], repo_dir: Path
) -> tuple[list[ReferenceEdge], list[Diagnostic]]:
    """One reference edge per joined pair and kind, between the endpoints of the two units.

    A unit that takes part in a join and has no endpoint is a row: nothing is dropped silently.
    """
    endpoints = endpoint_nodes(units, graphs, repo_dir)
    symbols = _symbols_by_file(graphs, repo_dir)
    by_id = {unit.id: unit for unit in units}
    sites: dict[tuple[str, str, object], list[dict]] = {}
    unreached: dict[str, Unit] = {}
    for found in joins:
        source = _enclosing(symbols, found.file, found.line) or endpoints.get(found.source)
        target = endpoints.get(found.target)
        for unit_id, node in ((found.source, source), (found.target, target)):
            if node is None and unit_id in by_id:
                unreached[unit_id] = by_id[unit_id]
        if source is None or target is None or source.fully_qualified_name == target.fully_qualified_name:
            continue
        key = (source.fully_qualified_name, target.fully_qualified_name, found.kind)
        site = {"file": found.file, "line": max(found.line, 1), "column": max(found.column, 1)}
        if site not in sites.setdefault(key, []):
            sites[key].append(site)
    edges = [
        ReferenceEdge(str(source), str(target), kind, tuple(sites[(source, target, kind)]))  # type: ignore[arg-type]
        for source, target, kind in sorted(sites, key=lambda key: (str(key[0]), str(key[1]), str(key[2])))
    ]
    return edges, [_no_box(unit, graphs, repo_dir) for _, unit in sorted(unreached.items())]


def endpoint_nodes(units: list[Unit], graphs: Mapping[str, CallGraph], repo_dir: Path) -> dict[str, Node]:
    """The node an arrow lands on for each unit: its manifest, keyed by its repository path.

    Three units get no endpoint: one with no manifest has nothing to point at, one whose directory
    holds no analysed code is a resource rather than a box, and one whose directory is the
    repository itself names the whole tree — an arrow into it would land on whichever component
    happens to hold the most code.
    """
    inside = languages_by_unit(units, graphs, repo_dir)
    return {
        unit.id: Node(unit.manifest, NodeType.FILE, str(repo_dir / unit.manifest), 1, 1)
        for unit in units
        if unit.manifest and unit.dir != "." and inside.get(unit.dir)
    }


def languages_by_unit(units: list[Unit], graphs: Mapping[str, CallGraph], repo_dir: Path) -> dict[str, dict[str, int]]:
    """How many analysed symbols each language has inside each unit, from one walk of the graphs.

    Why one walk: asking each unit in turn rescans every graph once per unit.
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


def place(hierarchy: ClusterScopeResult, units: list[Unit], edges: list[ReferenceEdge], repo_dir: Path) -> Placement:
    """The wiring graph for this hierarchy: every drawn edge with its two endpoint nodes, and their owners.

    An endpoint's owner is the deepest component whose files are inside the unit's directory, by
    plurality at every depth, so a relation between two services' children lands on the children
    rather than on their parents. A code symbol endpoint needs no owner: its component already owns
    it. A unit whose only edges are of an undrawn kind gets no node.
    """
    placement = Placement()
    graphs = hierarchy.graphs_by_language
    by_name = {node.fully_qualified_name: unit_id for unit_id, node in endpoint_nodes(units, graphs, repo_dir).items()}
    directories = {unit.id: unit.dir for unit in units}
    paths: dict[str, str] = {}
    for edge in edges:
        if not edge.kind.drawn:
            continue
        for name in (edge.src, edge.dst):
            if name in placement.graph.nodes:
                continue
            if name in by_name:
                placement.graph.add_node(Node(name, NodeType.FILE, str(repo_dir / name), 1, 1))
                owner = _owner(hierarchy, f"{directories[by_name[name]]}/", repo_dir, paths)
                if owner:
                    placement.owners[name] = owner
            else:
                symbol = next((graph.nodes[name] for graph in graphs.values() if name in graph.nodes), None)
                if symbol is not None:
                    placement.graph.add_node(symbol)
        if edge.src in placement.graph.nodes and edge.dst in placement.graph.nodes:
            placement.graph.add_reference_edge(edge)
    return placement


def _owner(scope: ClusterScopeResult, prefix: str, repo_dir: Path, paths: dict[str, str]) -> str:
    """The deepest group of this scope, and below it, holding most of the files under *prefix*."""
    counted: dict[str, int] = {}
    for group in scope.groups:
        inside = 0
        for language, members in group.symbol_members_by_language.items():
            graph = scope.graphs_by_language.get(language)
            if graph is None:
                continue
            for member in members:
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
        return ""
    winner = max(sorted(counted), key=lambda group_id: counted[group_id])
    group = next(group for group in scope.groups if group.group_id == winner)
    deeper = _owner(group.children, prefix, repo_dir, paths) if group.children is not None else ""
    return deeper or winner


def _symbols_by_file(graphs: Mapping[str, CallGraph], repo_dir: Path) -> dict[str, list[tuple[int, int, Node]]]:
    """Every callable and class by the repository path of its file, so a line finds its enclosing symbol."""
    found: dict[str, list[tuple[int, int, Node]]] = {}
    for graph in graphs.values():
        for node in graph.nodes.values():
            if node.type in CALLABLE_TYPES | CLASS_TYPES:
                found.setdefault(normalize_repo_path(node.file_path, repo_dir), []).append(
                    (node.line_start, node.line_end, node)
                )
    return found


def _enclosing(symbols: dict[str, list[tuple[int, int, Node]]], file: str, line: int) -> Node | None:
    """The smallest symbol of *file* whose span holds *line*, for an anchor that is a literal in code (§4)."""
    if os.path.splitext(file)[1] not in SOURCE_SUFFIXES:
        return None
    holding = [(end - start, start, node) for start, end, node in symbols.get(file, []) if start <= line <= end]
    if not holding:
        return None
    holding.sort(key=lambda item: (item[0], item[1], item[2].fully_qualified_name))
    return holding[0][2]


def _no_box(unit: Unit, graphs: Mapping[str, CallGraph], repo_dir: Path) -> Diagnostic:
    if not unit.manifest:
        reason = "has no manifest to land an arrow on"
    elif unit.dir == ".":
        reason = "is the repository itself, which no arrow can land on"
    else:
        reason = "holds no analysed code, so no arrow lands on it in P1"
    return Diagnostic(
        code=DiagnosticCode.NO_BOX_FOR_UNIT,
        message=f"{unit.id} {reason}",
        paths=(unit.manifest or unit.dir,),
        candidates=(unit.id,),
    )
