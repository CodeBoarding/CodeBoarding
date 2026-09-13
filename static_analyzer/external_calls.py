"""Finish the call edges an engine could not: answers in files it holds no symbols for.

A repository with several solutions runs one engine per solution root, and a call into a
project outside the caller's solution resolves to a file only the other engine named. A
warm start re-analyses only the changed files, and a call into an unchanged one resolves to
a file that analysis never named. Each engine keeps those answers; once the graphs are
merged, they are resolved here the way the engine resolves its own.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass

from static_analyzer.cfg import CallGraph
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.models import CallSite, ExternalCallSite
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.graph_definitions import (
    CALL,
    OVERRIDE,
    RECEIVER,
    CallTargets,
    GraphIndex,
    implemented_by,
    override_nodes,
    targets_for,
    targets_through,
)
from static_analyzer.internal_references import is_self_or_container_edge
from static_analyzer.node import Node

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LinkedCalls:
    """The edges linking added, and the sites no node in the graph could finish."""

    edges: list[tuple[str, str]]
    unresolved: list[ExternalCallSite]


def link_external_call_sites(
    call_graph: CallGraph,
    sites: list[ExternalCallSite],
    adapter: LanguageAdapter,
    inspector: SourceInspector,
    analysed_files: Collection[str],
    client: LSPClient | None = None,
) -> LinkedCalls:
    """Add the edges whose targets *call_graph* holds.

    *analysed_files* are the files the recording engine read, whose overrides it expanded itself.
    *client*, while its server is up, finishes the implementations a reached declaration is
    owed. A receiver site stands in only for a call no definition finished, as in the engine.
    """
    index = GraphIndex(call_graph, inspector)
    edges: list[tuple[str, str]] = []
    unresolved: list[ExternalCallSite] = []
    reached: set[tuple[str, str, int, int]] = set()
    owed: dict[tuple[str, int, int], list[tuple[Node, CallSite]]] = {}
    dispatched: dict[str, list[tuple[Node, CallSite]]] = {}
    for site in sorted(sites, key=lambda site: site.kind == RECEIVER):
        caller = call_graph.nodes.get(site.caller)
        if caller is None:
            continue
        if site.kind == OVERRIDE:
            dispatched.setdefault(site.member, []).append((caller, site.call_site))
            continue
        written = (site.caller, site.call_site.file, site.call_site.line, site.call_site.column)
        if site.kind == RECEIVER and written in reached:
            continue
        targets = _targets(index, site, adapter)
        if not targets.nodes:
            unresolved.append(site)
            continue
        reached.add(written)
        edges.extend(_add_edges(call_graph, caller, targets.nodes, site.call_site))
        for declaration in targets.implementations:
            owed.setdefault(declaration, []).append((caller, site.call_site))

    if client is not None and owed:
        for declaration, implementations in implemented_by(index, client, owed).items():
            for caller, call_site in owed[declaration]:
                edges.extend(_add_edges(call_graph, caller, implementations, call_site))

    for qualified_name, calls in dispatched.items():
        dispatched_from = call_graph.nodes.get(qualified_name)
        overrides = override_nodes(index, dispatched_from) if dispatched_from is not None else []
        elsewhere = [node for node in overrides if node.file_path not in analysed_files]
        for caller, call_site in calls if elsewhere else []:
            edges.extend(_add_edges(call_graph, caller, elsewhere, call_site))

    logger.info(
        "External call sites: %d kept, %d new edges, %d point outside this graph",
        len(sites),
        len(edges),
        len(unresolved),
    )
    return LinkedCalls(edges, unresolved)


def record_package_imports(
    package_dependencies: dict[str, dict], adapter: LanguageAdapter, edges: list[tuple[str, str]]
) -> None:
    """Record each linked edge that crosses packages: each engine derived its own before the merge."""
    for source, destination in edges:
        src_pkg, dst_pkg = adapter.extract_package(source), adapter.extract_package(destination)
        if src_pkg == dst_pkg:
            continue
        src_info = package_dependencies.get(src_pkg)
        dst_info = package_dependencies.get(dst_pkg)
        if src_info is not None and dst_pkg not in src_info.setdefault("imports", []):
            src_info["imports"].append(dst_pkg)
            src_info["imports"].sort()
        if dst_info is not None and src_pkg not in dst_info.setdefault("imported_by", []):
            dst_info["imported_by"].append(src_pkg)
            dst_info["imported_by"].sort()


def _targets(index: GraphIndex, site: ExternalCallSite, adapter: LanguageAdapter) -> CallTargets:
    if site.kind != RECEIVER:
        return targets_for(index, site.file, site.line, site.character, site.kind, adapter, site.call_site)
    receiver = index.declaration_at(site.file, site.line, site.character)
    member = index.call_graph.nodes.get(f"{receiver.fully_qualified_name}.{site.member}") if receiver else None
    return targets_through(index, member, CALL, adapter, site.call_site) if member else CallTargets.none()


def _add_edges(call_graph: CallGraph, caller: Node, targets: list[Node], call_site: CallSite) -> list[tuple[str, str]]:
    """The edges from *caller* to *targets* this call site adds, the way the engine rejects its own."""
    location = {"file": call_site.file, "line": call_site.line, "column": call_site.column}
    added: list[tuple[str, str]] = []
    for destination in targets:
        if is_self_or_container_edge(caller.fully_qualified_name, destination.fully_qualified_name):
            continue
        if (destination.file_path, destination.line_start) == (caller.file_path, caller.line_start):
            continue
        before = len(call_graph.edges)
        call_graph.add_edge(caller.fully_qualified_name, destination.fully_qualified_name, call_sites=[location])
        if len(call_graph.edges) > before:
            added.append((caller.fully_qualified_name, destination.fully_qualified_name))
    return added
