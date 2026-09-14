"""Pkl warm-start updater: bring cached per-language analysis up to date.

Warm-start flow:
1. Keep unchanged files from the pkl and invalidate changed/deleted files.
2. Re-run the engine on the changed files, resolving against the unchanged declarations, and merge
   its nodes, references and edges back in.
3. Restore cached inbound edges only when a definition query still proves them.
4. Keep unchanged-only edges cached and let ``StaticAnalyzer`` persist the new pkl.
"""

import logging
from pathlib import Path
from typing import Any

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_result import AnalysisData, InvalidatedEdge
from static_analyzer.analysis_cache import (
    invalidate_files,
    merge_results,
)
from static_analyzer.cfg import CallGraph
from static_analyzer.engine.call_graph_builder import CallGraphBuilder
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.models import CallSite, SymbolInfo
from static_analyzer.engine.result_converter import convert_to_codeboarding_format
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.utils import definition_location
from static_analyzer.graph_definitions import GraphIndex, implemented_by, potential_calls, targets_for

logger = logging.getLogger(__name__)


def update_cfg_for_changed_files(
    cached_analysis: dict[str, Any],
    changed_files: set[Path],
    adapter: LanguageAdapter,
    project_path: Path,
    repository_path: Path,
    engine_client: LSPClient,
    ignore_manager: RepoIgnoreManager,
) -> dict[str, Any]:
    """Apply *changed_files* to *cached_analysis* via re-LSP-and-merge.

    Steps:

    1. ``invalidate_files`` drops every node/edge/reference/class/package
       entry sourced from a changed file, leaving the cached state of every
       *unchanged* file intact.
    2. The engine re-analyses just the changed files (existing ones; deleted
       files contribute nothing) against the unchanged declarations, keeping every
       answer that lands outside this language's files for the final merge.
    3. ``merge_results`` unions the kept-from-cache state with the fresh
       per-file result.
    4. Cached callers into the changed files are re-proven.
    5. Surviving entries are filtered against the live filesystem so a
       deleted file's references / classes / package members are removed
       from the merged dict.

    Returns a fresh dict with the same shape as ``cached_analysis``. The
    caller stuffs it into ``StaticAnalysisResults`` and saves the pkl
    tagged with the *current* source SHA.
    """
    if not changed_files:
        return cached_analysis

    existing_files = {f for f in changed_files if f.exists()}
    deleted_files = {f for f in changed_files if not f.exists()}
    logger.info(
        "update_cfg_for_changed_files: %d changed (%d existing, %d deleted)",
        len(changed_files),
        len(existing_files),
        len(deleted_files),
    )

    updated_cache = invalidate_files(cached_analysis, changed_files)

    changed_source_files = [
        f for f in existing_files if f.suffix in adapter.file_extensions and not ignore_manager.should_ignore(f)
    ]

    if changed_source_files:
        builder = CallGraphBuilder(engine_client, adapter, project_path, repository_path)
        # Why the unchanged declarations: a changed file's call into an unchanged one then resolves
        # inside the engine's phases, exactly as a full build resolves it.
        engine_result = builder.build(
            changed_source_files, known_declarations=_declarations(updated_cache.analysis.call_graph)
        )
        new_analysis = convert_to_codeboarding_format(builder.symbol_table, engine_result, adapter)
    else:
        new_analysis = {
            "call_graph": CallGraph(language=adapter.language),
            "class_hierarchies": {},
            "package_relations": {},
            "references": [],
            "source_files": [],
            "diagnostics": {},
            "external_call_sites": [],
        }

    fresh_diagnostics = engine_client.get_collected_diagnostics()
    if fresh_diagnostics:
        new_analysis["diagnostics"] = fresh_diagnostics

    merged_analysis = merge_results(updated_cache.analysis, new_analysis)
    source_inspector = SourceInspector()
    index = GraphIndex(merged_analysis.call_graph)
    _restore_inbound_edges_via_definitions(
        index,
        updated_cache.invalidated_edges,
        updated_cache.invalidated_files,
        adapter,
        engine_client,
        source_inspector,
    )
    updated = _filter_to_live_files(merged_analysis).to_dict()
    # What still lies outside is another engine's, for the final merge to link.
    updated["external_call_sites"] = new_analysis["external_call_sites"]
    return updated


def _restore_inbound_edges_via_definitions(
    index: GraphIndex,
    invalidated_edges: list[InvalidatedEdge],
    changed_file_strs: set[str],
    adapter: LanguageAdapter,
    engine_client: LSPClient,
    source_inspector: SourceInspector,
) -> None:
    """Re-resolve invalidated edges whose source file is unchanged, from their cached call sites.

    The cached call sites let us ask the same question a full run asks: does this
    position still resolve to that destination? Restoring without asking is not
    enough, an unchanged caller can still lose the edge when the destination's
    own declaration moves out from under it.

    The outbound direction is absent here on purpose: the partial build resolves every
    changed call site against the unchanged declarations.
    """
    call_graph = index.call_graph
    pending: dict[tuple[str, str], list[dict[str, str | int]]] = {}
    for src_name, dst_name, old_src_node, _old_dst_node, cached_sites in invalidated_edges:
        if old_src_node.file_path in changed_file_strs:
            continue
        if not call_graph.has_node(src_name) or not call_graph.has_node(dst_name):
            continue
        if cached_sites:
            pending[(src_name, dst_name)] = cached_sites
    if not pending:
        return

    for file_path in {str(site["file"]) for sites in pending.values() for site in sites}:
        try:
            engine_client.did_open(Path(file_path))
        except Exception:
            logger.debug("Failed to open %s while restoring cached edges", file_path, exc_info=True)

    # A cached site is re-asked as the shape it was written as: a construction reaches the
    # constructors, a collection initializer reaches ``Add``, a loop asks for the type it
    # enumerates. The caller's file is unchanged, so its source still says which is which.
    potential_by_file = {
        file_path: potential_calls(Path(file_path), source_inspector, adapter, index.callable_names)
        for file_path in {str(site["file"]) for sites in pending.values() for site in sites}
    }
    by_request: dict[str, list[tuple[tuple[str, str], dict[str, str | int], CallSite, str]]] = {}
    for edge, sites in pending.items():
        for site in sites:
            call_site = CallSite(str(site["file"]), int(site["line"]), int(site["column"]))
            for method, kind in potential_by_file[call_site.file].requests_at(
                (call_site.lsp_line, call_site.lsp_column)
            ):
                by_request.setdefault(method, []).append((edge, site, call_site, kind))

    confirmed: dict[tuple[str, str], list[dict[str, str | int]]] = {}
    unproven: dict[tuple[str, int, int], list[tuple[tuple[str, str], dict[str, str | int]]]] = {}
    for method, entries in by_request.items():
        send = (
            engine_client.send_type_definition_batch
            if method == "type_definition"
            else engine_client.send_definition_batch
        )
        answers = send(
            [(Path(call_site.file), call_site.lsp_line, call_site.lsp_column) for _, _, call_site, _ in entries]
        )
        for (edge, site, call_site, kind), definitions in zip(entries, answers):
            # A polymorphic call resolves to the interface or base declaration, never
            # to the implementation the cached edge names, so exact equality alone
            # drops every caller-to-implementation edge whose implementation file
            # changed -- silently, while the update reports success.
            reachable: set[str] = set()
            owed: list[tuple[str, int, int]] = []
            for definition in definitions:
                location = definition_location(definition)
                if location is None:
                    continue
                targets = targets_for(
                    index, source_inspector, str(location[0]), location[1], location[2], kind, adapter, call_site
                )
                reachable.update(node.fully_qualified_name for node in targets.nodes)
                owed.extend(targets.implementations)
            if edge[1] in reachable:
                confirmed.setdefault(edge, []).append(site)
                continue
            for declaration in owed:
                unproven.setdefault(declaration, []).append((edge, site))

    for declaration, nodes in implemented_by(index, source_inspector, engine_client, unproven).items():
        names = {node.fully_qualified_name for node in nodes}
        for edge, site in unproven[declaration]:
            if edge[1] in names:
                confirmed.setdefault(edge, []).append(site)

    restored = 0
    for (src_name, dst_name), sites in confirmed.items():
        try:
            call_graph.add_edge(src_name, dst_name, call_sites=sites)
            restored += 1
        except ValueError:
            logger.debug("Failed to restore edge %s -> %s", src_name, dst_name, exc_info=True)

    logger.info("Restored %d of %d cached inbound edge(s) via definitions", restored, len(pending))


def _filter_to_live_files(merged_analysis: AnalysisData) -> AnalysisData:
    """Drop entries whose file no longer exists on disk.

    A file in ``source_files`` may have been re-LSPed earlier in the run and
    then removed by a subsequent edit; this final filter keeps the merged
    dict consistent with the live filesystem.
    """
    # Normalize: ``merge_results`` may contain a mix of Path (from the cached
    # side) and str (from the LSP-rebuilt new side); coerce before ``.exists()``.
    all_existing = {Path(f) for f in merged_analysis.source_files if Path(f).exists()}
    existing_file_strs = {str(f) for f in all_existing}

    merged_analysis.source_files = list(all_existing)
    merged_analysis.references = [ref for ref in merged_analysis.references if ref.file_path in existing_file_strs]

    merged_analysis.call_graph = merged_analysis.call_graph.filter(lambda node: node.file_path in existing_file_strs)

    # Hierarchy entries are keyed by class qname and carry no file_path, so filter by whether the
    # class still exists as a live call-graph node (already filtered above) — the old
    # ``info.get("file_path")`` check was always None and dropped every hierarchy, which then
    # starved the warm-start INHERITS re-derivation of its source on the next run.
    live_class_names = set(merged_analysis.call_graph.nodes.keys())
    merged_analysis.class_hierarchies = {
        name: info for name, info in merged_analysis.class_hierarchies.items() if name in live_class_names
    }

    filtered_packages: dict[str, Any] = {}
    for pkg_name, pkg_info in merged_analysis.package_relations.items():
        existing_pkg_files = [f for f in pkg_info.get("files", []) if f in existing_file_strs]
        if existing_pkg_files:
            filtered_packages[pkg_name] = {**pkg_info, "files": existing_pkg_files}
    merged_analysis.package_relations = filtered_packages

    return merged_analysis


def _declarations(call_graph: CallGraph) -> list[SymbolInfo]:
    """The graph's nodes as symbols a partial build resolves against but never outputs.

    Positions are the engine's own: a zero-based line and the name's column. A declaration's owner is
    the node its qualified name nests under, which a call on a member reaches along with it.
    """
    declarations: list[SymbolInfo] = []
    for qualified_name, node in call_graph.nodes.items():
        owner_name = qualified_name.split("(", 1)[0].rpartition(".")[0]
        owner = call_graph.nodes.get(owner_name) if owner_name else None
        declarations.append(
            SymbolInfo(
                name=qualified_name[len(owner_name) + 1 :] if owner_name else qualified_name,
                qualified_name=qualified_name,
                kind=node.type,
                file_path=Path(node.file_path),
                start_line=node.line_start - 1,
                start_char=node.col_start,
                end_line=node.line_end - 1,
                end_char=0,
                parent_chain=[(owner_name.rpartition(".")[2], owner.type)] if owner is not None else [],
                owner_qualified_name=owner_name if owner is not None else "",
            )
        )
    return declarations
