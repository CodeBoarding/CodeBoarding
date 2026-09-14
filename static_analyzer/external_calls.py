"""Finish the call edges an engine could not: answers in files it holds no symbols for.

A repository with several solutions runs one engine per solution root, and a call into a
project outside the caller's solution resolves to a file only the other engine named. A
warm start re-analyses only the changed files, and a call into an unchanged one resolves to
a file that analysis never named. Each engine keeps those answers; once the graphs are
merged, they are resolved here the way the engine resolves its own.

Linking asks the language server nothing. Where it runs:

- Full build: ``StaticAnalyzer._absorb_and_link``, after every engine merged. The owning
  servers may already be down, so implementations a linked call is still owed are not asked.
- Warm start: ``update_cfg_for_changed_files`` links the partial build's answers, asks the live
  server for the implementations they are owed (``implemented_by``), and links those with
  ``link_implementations``.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass

from static_analyzer.cfg import CallGraph
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.models import CallSite, ExternalCallSite
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.graph_definitions import (
    CALL,
    OVERRIDE,
    RECEIVER,
    CallTargets,
    GraphIndex,
    override_nodes,
    targets_for,
    targets_through,
)
from static_analyzer.internal_references import is_self_or_container_edge
from static_analyzer.node import Node

logger = logging.getLogger(__name__)

ImplementationsOwed = dict[tuple[str, int, int], list[tuple[Node, CallSite]]]
"""Declarations still owed an implementation query, by position, with the calls that reached them."""


@dataclass(frozen=True)
class LinkedCalls:
    """The edges linking added, the sites no node in the graph could finish, and the queries still owed."""

    edges: list[tuple[str, str]]
    unresolved: list[ExternalCallSite]
    owed: ImplementationsOwed


def link_external_call_sites(
    index: GraphIndex,
    sites: list[ExternalCallSite],
    adapter: LanguageAdapter,
    inspector: SourceInspector,
    analysed_files: Collection[str],
) -> LinkedCalls:
    """Add the edges whose targets the indexed graph holds.

    *analysed_files* are the files the recording engine read, whose overrides it expanded itself.
    A receiver site stands in only for a call no definition finished, as in the engine.
    """
    call_graph = index.call_graph
    edges: list[tuple[str, str]] = []
    unresolved: list[ExternalCallSite] = []
    reached: set[tuple[str, str, int, int]] = set()
    owed: ImplementationsOwed = {}
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
        targets = _targets(index, inspector, site, adapter)
        if not targets.nodes:
            unresolved.append(site)
            continue
        reached.add(written)
        edges.extend(_add_edges(call_graph, caller, targets.nodes, site.call_site))
        for declaration in targets.implementations:
            owed.setdefault(declaration, []).append((caller, site.call_site))

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
    return LinkedCalls(edges, unresolved, owed)


def link_implementations(
    call_graph: CallGraph, owed: ImplementationsOwed, implementations: dict[tuple[str, int, int], list[Node]]
) -> list[tuple[str, str]]:
    """Add an edge from every call that reached a declaration to each node implementing it."""
    edges: list[tuple[str, str]] = []
    for declaration, nodes in implementations.items():
        for caller, call_site in owed.get(declaration, []):
            edges.extend(_add_edges(call_graph, caller, nodes, call_site))
    return edges


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


def _targets(
    index: GraphIndex, inspector: SourceInspector, site: ExternalCallSite, adapter: LanguageAdapter
) -> CallTargets:
    if site.kind != RECEIVER:
        return targets_for(index, inspector, site.file, site.line, site.character, site.kind, adapter, site.call_site)
    receiver = index.declaration_at(inspector, site.file, site.line, site.character)
    member = index.call_graph.nodes.get(f"{receiver.fully_qualified_name}.{site.member}") if receiver else None
    return targets_through(index, inspector, member, CALL, adapter, site.call_site) if member else CallTargets.none()


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
