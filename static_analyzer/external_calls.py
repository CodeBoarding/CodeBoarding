"""Finish the call edges an engine could not: answers in files it holds no symbols for.

A repository with several solutions runs one engine per solution root, and a call into a
project outside the caller's solution resolves to a file only the other engine named. A
warm start re-analyses only the changed files, and a call into an unchanged one resolves to
a file that analysis never named. Each engine keeps those answers; once the graphs are
merged, they are resolved here the way the engine resolves its own.

Linking asks the language server nothing. Where it runs:

- Full build: ``StaticAnalyzer._absorb_and_link``, after every engine merged. The owning
  servers may already be down, so implementations a linked call is still owed are not asked.
- Warm start: the partial build resolves against the unchanged declarations itself, so only its
  answers in other engines' files reach here, at that same final merge.

What no graph of the caller's language holds may lie in another language's files, as a
Kotlin call into a Java class does. Those calls become cross-language edges, since a
language's call graph holds only its own nodes.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass, replace
from pathlib import Path

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph, Edge
from static_analyzer.config import Language
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


@dataclass(frozen=True)
class LinkedCalls:
    """The edges linking added, and the sites no node in the graph could finish."""

    edges: list[tuple[str, str]]
    unresolved: list[ExternalCallSite]


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


def link_across_languages(
    results: StaticAnalysisResults,
    language: Language,
    sites: list[ExternalCallSite],
    adapter: LanguageAdapter,
    inspector: SourceInspector,
) -> list[Edge]:
    """Edges from *language*'s callers to the declarations of another language's graph the *sites* name.

    *adapter* is the caller's, since how a site is finished depends on how its server answered.
    An answer at the start of a declaration's text names that declaration: a server may answer a
    target with the whole element, doc comment and annotations included.
    An override site is left out: overrides in another language's files would need that
    language's view of these types, which its engine never reads.
    """
    callers = results.get_cfg(language).nodes
    indexes: dict[Language, GraphIndex] = {}
    edges: dict[tuple[str, str], Edge] = {}
    reached: set[tuple[str, str, int, int]] = set()
    for site in sorted(sites, key=lambda site: site.kind == RECEIVER):
        target_language = results.results_language_of(site.file)
        caller = callers.get(site.caller)
        if site.kind == OVERRIDE or caller is None or target_language is None or target_language == language:
            continue
        written = (site.caller, site.call_site.file, site.call_site.line, site.call_site.column)
        if site.kind == RECEIVER and written in reached:
            continue
        index = indexes.get(target_language)
        if index is None:
            try:
                index = indexes[target_language] = GraphIndex(results.get_cfg(target_language))
            except ValueError:
                continue
        targets = _targets(index, inspector, site, adapter)
        named = (
            None if targets.nodes else inspector.declaration_name_from_start(Path(site.file), site.line, site.character)
        )
        if named is not None:
            targets = _targets(index, inspector, replace(site, line=named[0], character=named[1]), adapter)
        if targets.nodes:
            reached.add(written)
        location = {"file": site.call_site.file, "line": site.call_site.line, "column": site.call_site.column}
        for target in targets.nodes:
            edge = edges.setdefault((caller.fully_qualified_name, target.fully_qualified_name), Edge(caller, target))
            edge.add_call_site(location)
    logger.info("Cross-language call sites from %s: %d kept, %d edges", language.value, len(sites), len(edges))
    return list(edges.values())


def callers_of_changed_targets(
    cached: StaticAnalysisResults, changed_files: Collection[Path], language: Language
) -> set[Path]:
    """The *language* files whose cached cross-language calls end in a changed file.

    Why: an unchanged caller can bind elsewhere once its target's file changes (a new overload), so
    it is analysed again, as a changed file's callers are within one language.
    """
    changed = {str(path) for path in changed_files}
    return {
        Path(edge.src_node.file_path)
        for edge in cached.cross_language_edges
        if edge.dst_node.file_path in changed and cached.results_language_of(edge.src_node.file_path) == language
    }


def carry_cross_language_edges(
    cached: StaticAnalysisResults,
    results: StaticAnalysisResults,
    reanalysed_files: Collection[str],
    rebuilt_languages: Collection[Language],
) -> list[Edge]:
    """The cached cross-language edges a warm start keeps, re-pointed at the rebuilt graphs' nodes.

    An edge is kept while both its ends still exist and its caller's file was not analysed
    again, since a re-analysed file reports its cross-language calls anew.
    """
    kept: list[Edge] = []
    for edge in cached.cross_language_edges:
        source_language = results.results_language_of(edge.src_node.file_path)
        if edge.src_node.file_path in reanalysed_files or source_language in rebuilt_languages:
            continue
        source = _node_named(results, edge.src_node)
        destination = _node_named(results, edge.dst_node)
        if source is not None and destination is not None:
            kept.append(Edge(source, destination, edge.call_sites))
    return kept


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


def _node_named(results: StaticAnalysisResults, node: Node) -> Node | None:
    language = results.results_language_of(node.file_path)
    if language is None:
        return None
    try:
        return results.get_cfg(language).nodes.get(node.fully_qualified_name)
    except ValueError:
        return None


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
