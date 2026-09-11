"""Finish the call edges one engine could not: definitions in another engine's files.

A repository with several solutions runs one engine per solution root, and a call
into a project outside the caller's solution resolves to a file only the other
engine named. Each engine keeps those positions; once every graph is merged, the
positions are resolved here the way the engine resolves its own.
"""

from __future__ import annotations

import logging

from static_analyzer.cfg import CallGraph
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.models import ExternalCallSite
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.graph_definitions import CALL, GraphIndex, targets_for
from static_analyzer.internal_references import is_self_or_container_edge

logger = logging.getLogger(__name__)


def link_external_call_sites(
    call_graph: CallGraph,
    sites: list[ExternalCallSite],
    adapter: LanguageAdapter,
    package_dependencies: dict[str, dict],
    inspector: SourceInspector,
) -> int:
    """Add the edges whose targets the merged graph now holds; return how many were added.

    A new edge across packages is recorded in *package_dependencies* too: each engine
    derived those from its own edges before the merge, and the health checks read them.
    """
    if not sites:
        return 0
    index = GraphIndex(call_graph, inspector)
    added = 0
    unresolved = 0
    for site in sites:
        caller = call_graph.nodes.get(site.caller)
        if caller is None:
            continue
        constructing = (
            site.kind == CALL and adapter.expands_constructors and inspector.is_construction_site(site.call_site)
        )
        targets = targets_for(index, site.file, site.line, site.character, site.kind, adapter, constructing).nodes
        if not targets:
            unresolved += 1
            continue
        call_site = {"file": site.call_site.file, "line": site.call_site.line, "column": site.call_site.column}
        for destination in targets:
            if is_self_or_container_edge(caller.fully_qualified_name, destination.fully_qualified_name):
                continue
            if (destination.file_path, destination.line_start) == (caller.file_path, caller.line_start):
                continue
            before = len(call_graph.edges)
            call_graph.add_edge(caller.fully_qualified_name, destination.fully_qualified_name, call_sites=[call_site])
            if len(call_graph.edges) > before:
                added += 1
                _record_package_import(
                    package_dependencies, adapter, caller.fully_qualified_name, destination.fully_qualified_name
                )
    logger.info(
        "Cross-engine call sites: %d linked into %d new edges, %d point outside every engine's files (%s)",
        len(sites) - unresolved,
        added,
        unresolved,
        index.counts.summary(),
    )
    return added


def _record_package_import(
    package_dependencies: dict[str, dict], adapter: LanguageAdapter, source: str, destination: str
) -> None:
    src_pkg, dst_pkg = adapter.extract_package(source), adapter.extract_package(destination)
    if src_pkg == dst_pkg:
        return
    src_info = package_dependencies.get(src_pkg)
    dst_info = package_dependencies.get(dst_pkg)
    if src_info is not None and dst_pkg not in src_info.setdefault("imports", []):
        src_info["imports"].append(dst_pkg)
        src_info["imports"].sort()
    if dst_info is not None and src_pkg not in dst_info.setdefault("imported_by", []):
        dst_info["imported_by"].append(src_pkg)
        dst_info["imported_by"].sort()
