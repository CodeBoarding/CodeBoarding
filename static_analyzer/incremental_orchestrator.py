"""Pkl warm-start updater: bring cached per-language analysis up to date.

Warm-start flow:
1. Keep unchanged files from the pkl and invalidate changed/deleted files.
2. Re-LSP existing changed files and merge their fresh nodes/references back in.
3. Restore cached inbound edges only when a definition query still proves them.
4. Add new outbound edges by resolving changed-file call sites with definitions.
5. Keep unchanged-only edges cached and let ``StaticAnalyzer`` persist the new pkl.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_result import AnalysisData, InvalidatedEdge
from static_analyzer.analysis_cache import (
    invalidate_files,
    merge_results,
)
from static_analyzer.engine.call_graph_builder import CallGraphBuilder
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.result_converter import convert_to_codeboarding_format
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.models import CallSite, ExternalCallSite
from static_analyzer.engine.utils import definition_location
from static_analyzer.graph_definitions import (
    CALL,
    COLLECTION_INITIALIZER,
    ITERATED,
    METHOD_GROUP,
    GraphIndex,
    containing_source_node,
    definition_nodes,
    implementation_positions,
    targets_for,
)
from static_analyzer.cfg import CallGraph
from static_analyzer.internal_references import is_self_or_container_edge
from static_analyzer.node import Node

logger = logging.getLogger(__name__)

_DEFINITION_BATCH_SIZE = 50


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
    2. The LSP re-analyses just the changed files (existing ones; deleted
       files contribute nothing).
    3. ``merge_results`` unions the kept-from-cache state with the fresh
       per-file result.
    4. Surviving entries are filtered against the live filesystem so a
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
        engine_result = builder.build(changed_source_files)
        new_analysis = convert_to_codeboarding_format(builder.symbol_table, engine_result, adapter)
    else:
        new_analysis = {
            "call_graph": CallGraph(language=adapter.language),
            "class_hierarchies": {},
            "package_relations": {},
            "references": [],
            "source_files": [],
            "diagnostics": {},
        }

    fresh_diagnostics = engine_client.get_collected_diagnostics()
    if fresh_diagnostics:
        new_analysis["diagnostics"] = fresh_diagnostics

    merged_analysis = merge_results(updated_cache.analysis, new_analysis)
    external = _rebuild_changed_file_edges(
        merged_analysis,
        updated_cache.invalidated_edges,
        updated_cache.invalidated_files,
        changed_source_files,
        adapter,
        engine_client,
    )
    updated = _filter_to_live_files(merged_analysis).to_dict()
    updated["external_call_sites"] = external
    return updated


def _rebuild_changed_file_edges(
    merged_analysis: AnalysisData,
    invalidated_edges: list[InvalidatedEdge],
    changed_file_strs: set[str],
    changed_source_files: list[Path],
    adapter: LanguageAdapter,
    engine_client: LSPClient,
) -> list[ExternalCallSite]:
    """Restore and re-resolve the edges an edit touched; return the definitions this graph cannot name yet.

    Why return them: a changed caller in one solution and its new destination in another are
    updated by different engines, in either order, so a definition with no node here is only
    finished once every engine's graph is merged.
    """
    source_inspector = SourceInspector()
    index = GraphIndex(merged_analysis.call_graph, source_inspector)
    _restore_inbound_edges_via_definitions(
        index, invalidated_edges, changed_file_strs, adapter, engine_client, source_inspector
    )
    external = _add_outbound_edges_from_changed_files(
        index,
        changed_source_files,
        engine_client,
        source_inspector,
        adapter,
    )
    logger.info("Warm-start definition matches: %s", index.counts.summary())
    return external


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

    The outbound direction is absent here on purpose:
    ``_add_outbound_edges_from_changed_files`` re-resolves those from live source.
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
    shapes_by_file = {
        file_path: _call_shapes(Path(file_path), source_inspector, adapter)
        for file_path in {str(site["file"]) for sites in pending.values() for site in sites}
    }
    by_request: dict[str, list[tuple[tuple[str, str], dict[str, str | int], str, bool]]] = {}
    for edge, sites in pending.items():
        for site in sites:
            file_path = str(site["file"])
            position = (int(site["line"]) - 1, int(site["column"]) - 1)
            kind = shapes_by_file[file_path].kind_at(position)
            constructing = (
                kind == CALL
                and adapter.expands_constructors
                and source_inspector.is_construction_site(CallSite(file_path, int(site["line"]), int(site["column"])))
            )
            method = "type_definition" if kind == ITERATED else "definition"
            by_request.setdefault(method, []).append((edge, site, kind, constructing))

    confirmed: dict[tuple[str, str], list[dict[str, str | int]]] = {}
    unproven: dict[tuple[str, int, int], list[tuple[tuple[str, str], dict[str, str | int]]]] = {}
    for method, entries in by_request.items():
        send = (
            engine_client.send_type_definition_batch
            if method == "type_definition"
            else engine_client.send_definition_batch
        )
        for start in range(0, len(entries), _DEFINITION_BATCH_SIZE):
            batch = entries[start : start + _DEFINITION_BATCH_SIZE]
            results = send([(Path(str(s["file"])), int(s["line"]) - 1, int(s["column"]) - 1) for _, s, _, _ in batch])
            for (edge, site, kind, constructing), definitions in zip(batch, results):
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
                        index, str(location[0]), location[1], location[2], kind, adapter, constructing
                    )
                    reachable.update(node.fully_qualified_name for node in targets.nodes)
                    owed.extend(targets.implementations)
                if edge[1] in reachable:
                    confirmed.setdefault(edge, []).append(site)
                    continue
                for impl_position in owed:
                    unproven.setdefault(impl_position, []).append((edge, site))

    for impl_position, nodes in _expand_implementations(index, engine_client, list(unproven)).items():
        names = {node.fully_qualified_name for node in nodes}
        for edge, site in unproven[impl_position]:
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


@dataclass(frozen=True)
class _CallShapes:
    """Which shape each call site in one file has, so a site resolves the way it was found.

    The outbound pass reads them off a changed file it is about to query; the restoration
    pass reads them off an unchanged caller whose cached sites it is re-asking. Both have to
    reach the same answer or a cached edge comes back smaller than the one a rebuild builds.
    """

    call_sites: list[CallSite]
    method_group: set[tuple[int, int]]
    collection: set[tuple[int, int]]
    iterated: set[tuple[int, int]]

    def kind_at(self, position: tuple[int, int]) -> str:
        if position in self.iterated:
            return ITERATED
        if position in self.method_group:
            return METHOD_GROUP
        if position in self.collection:
            return COLLECTION_INITIALIZER
        return CALL


def _call_shapes(file_path: Path, source_inspector: SourceInspector, adapter: LanguageAdapter) -> _CallShapes:
    """Every call site the file writes, and the shape of each, by the adapter's capabilities."""
    call_sites = source_inspector.find_call_sites(file_path)
    method_group: set[tuple[int, int]] = set()
    if adapter.resolves_method_groups:
        known = {(site.lsp_line, site.lsp_column) for site in call_sites}
        for site in source_inspector.find_method_group_sites(file_path):
            if (site.lsp_line, site.lsp_column) in known:
                continue
            method_group.add((site.lsp_line, site.lsp_column))
            call_sites.append(site)
    collection: set[tuple[int, int]] = set()
    if adapter.resolves_collection_initializers:
        collection = {
            (site.lsp_line, site.lsp_column) for site in source_inspector.find_collection_initializer_sites(file_path)
        }
    iterated: set[tuple[int, int]] = set()
    if adapter.resolves_iterated_types:
        iterated = {
            (site.lsp_line, site.lsp_column) for site in source_inspector.find_iterated_expression_sites(file_path)
        }
    return _CallShapes(call_sites, method_group, collection, iterated)


def _add_outbound_edges_from_changed_files(
    index: GraphIndex,
    changed_source_files: list[Path],
    engine_client: LSPClient,
    source_inspector: SourceInspector,
    adapter: LanguageAdapter,
) -> list[ExternalCallSite]:
    call_graph = index.call_graph
    external: list[ExternalCallSite] = []
    if not changed_source_files:
        return external
    added = 0
    changed_file_strs = {str(file_path) for file_path in changed_source_files}
    pending: dict[tuple[str, int, int], list[tuple[Node, CallSite]]] = {}
    for file_path in changed_source_files:
        shapes = _call_shapes(file_path, source_inspector, adapter)
        call_sites = shapes.call_sites
        added += _add_iterated_type_edges(index, file_path, engine_client, source_inspector, adapter, external)
        if not call_sites:
            continue
        queries = [(file_path, site.lsp_line, site.lsp_column) for site in call_sites]
        definition_results = engine_client.send_definition_batch(queries)
        unresolved: list[CallSite] = []
        for site, definitions in zip(call_sites, definition_results):
            src_node = containing_source_node(index, str(file_path), site.lsp_line, site.lsp_column)
            if src_node is None:
                continue
            kind = shapes.kind_at((site.lsp_line, site.lsp_column))
            constructing = kind == CALL and adapter.expands_constructors and source_inspector.is_construction_site(site)
            reached = False
            for definition in definitions:
                location = definition_location(definition)
                if location is None:
                    continue
                targets = targets_for(index, str(location[0]), location[1], location[2], kind, adapter, constructing)
                if not targets.nodes:
                    external.append(
                        ExternalCallSite(
                            src_node.fully_qualified_name, str(location[0]), location[1], location[2], site, kind
                        )
                    )
                    continue
                reached = True
                added += _add_edges(call_graph, src_node, targets.nodes, site, changed_file_strs)
                if str(location[0]) not in changed_file_strs:
                    # The partial build could not name this declaration, so nothing reached
                    # through it is its business either -- however changed the file it lands in.
                    for impl_position in targets.implementations:
                        pending.setdefault(impl_position, []).append((src_node, site))
            if not reached and kind == CALL:
                unresolved.append(site)
        added += _add_receiver_member_edges(
            index, file_path, unresolved, engine_client, source_inspector, changed_file_strs, pending
        )
    owned_by_partial: set[str] = set()
    for impl_position, nodes in _expand_implementations(index, engine_client, list(pending)).items():
        for src_node, site in pending[impl_position]:
            added += _add_edges(call_graph, src_node, nodes, site, owned_by_partial)
    if added:
        logger.info("Added %d new outbound edge(s) from changed files", added)
    return external


def _expand_implementations(
    index: GraphIndex, engine_client: LSPClient, positions: list[tuple[str, int, int]]
) -> dict[tuple[str, int, int], list[Node]]:
    """The nodes implementing each declaration, keyed by the position it is declared at.

    A full build follows every callable it resolves with ``textDocument/implementation`` and
    gives the caller an edge to each result. Without the same step here an edit alone would
    drop every caller-to-implementation edge, and the warm graph would differ from a rebuild
    of the same tree.
    """
    found: dict[tuple[str, int, int], list[Node]] = {}
    for start in range(0, len(positions), _DEFINITION_BATCH_SIZE):
        batch = positions[start : start + _DEFINITION_BATCH_SIZE]
        results = engine_client.send_implementation_batch(
            [(Path(file_path), line, character) for file_path, line, character in batch]
        )
        for position, implementations in zip(batch, results):
            nodes = [node for result in implementations for node in definition_nodes(index, result)]
            if nodes:
                found[position] = nodes
    return found


def _add_iterated_type_edges(
    index: GraphIndex,
    file_path: Path,
    engine_client: LSPClient,
    source_inspector: SourceInspector,
    adapter: LanguageAdapter,
    external: list[ExternalCallSite],
) -> int:
    """``foreach (var x in bag)`` calls ``bag.GetEnumerator()`` with no call written.

    A definition query at that position returns the variable, so the full
    rebuild asks for the type instead. Without the same pass here, every
    enumerator edge in a changed file disappears until a full rebuild.
    """
    if not adapter.resolves_iterated_types:
        return 0
    sites = source_inspector.find_iterated_expression_sites(file_path)
    if not sites:
        return 0
    results = engine_client.send_type_definition_batch([(file_path, site.lsp_line, site.lsp_column) for site in sites])
    added = 0
    for site, definitions in zip(sites, results):
        src_node = containing_source_node(index, str(file_path), site.lsp_line, site.lsp_column)
        if src_node is None:
            continue
        for definition in definitions:
            location = definition_location(definition)
            if location is None:
                continue
            targets = targets_for(index, str(location[0]), location[1], location[2], ITERATED, adapter).nodes
            if not targets:
                external.append(
                    ExternalCallSite(
                        src_node.fully_qualified_name, str(location[0]), location[1], location[2], site, ITERATED
                    )
                )
                continue
            added += _add_edges(index.call_graph, src_node, targets, site, {str(file_path)})
    return added


def _add_receiver_member_edges(
    index: GraphIndex,
    file_path: Path,
    unresolved: list[CallSite],
    engine_client: LSPClient,
    source_inspector: SourceInspector,
    changed_file_strs: set[str],
    pending_implementations: dict[tuple[str, int, int], list[tuple[Node, CallSite]]],
) -> int:
    """``receiver.member(...)`` whose member left the repository: name it through the receiver.

    The full rebuild does the same in ``edge_builder._resolve_through_receivers``; without it
    here, every such edge in a changed file disappears until the next full run.
    """
    if not unresolved:
        return 0
    receivers = source_inspector.receiver_member_calls(file_path)
    # One query per receiver, not per call: `log.warn` and `log.info` name the same object.
    by_receiver: dict[tuple[int, int], list[tuple[CallSite, str]]] = {}
    for site in unresolved:
        found = receivers.get((site.lsp_line, site.lsp_column))
        if found is not None:
            by_receiver.setdefault((found.line, found.column), []).append((site, found.member))
    if not by_receiver:
        return 0

    positions = list(by_receiver)
    added = 0
    for start in range(0, len(positions), _DEFINITION_BATCH_SIZE):
        batch = positions[start : start + _DEFINITION_BATCH_SIZE]
        results = engine_client.send_definition_batch([(file_path, line, column) for line, column in batch])
        for position, definitions in zip(batch, results):
            for definition in definitions:
                location = definition_location(definition)
                if location is None:
                    continue
                receiver = index.declaration_at(str(location[0]), location[1], location[2]).declaration
                if receiver is None:
                    continue
                for site, member in by_receiver[position]:
                    target = index.call_graph.nodes.get(f"{receiver.fully_qualified_name}.{member}")
                    src_node = containing_source_node(index, str(file_path), site.lsp_line, site.lsp_column)
                    if target is not None and src_node is not None:
                        added += _add_edges(index.call_graph, src_node, [target], site, changed_file_strs)
                        for impl_position in implementation_positions([target]):
                            pending_implementations.setdefault(impl_position, []).append((src_node, site))
    return added


def _add_edges(
    call_graph: CallGraph, src_node: Node, targets: list[Node], site: CallSite, owned_by_partial: set[str]
) -> int:
    """Edges from *src_node* to *targets* with *site*.

    ``owned_by_partial`` names the files whose edges the fresh partial analysis already built.
    Pass it empty for a target the partial build had no route to, so that its file being
    changed does not read as "already done".
    """
    added = 0
    for dst_node in targets:
        if dst_node.file_path in owned_by_partial:
            continue
        if is_self_or_container_edge(src_node.fully_qualified_name, dst_node.fully_qualified_name):
            continue
        try:
            before = len(call_graph.edges)
            call_graph.add_edge(
                src_node.fully_qualified_name,
                dst_node.fully_qualified_name,
                call_sites=[{"file": site.file, "line": site.line, "column": site.column}],
            )
            if len(call_graph.edges) > before:
                added += 1
        except ValueError:
            logger.debug("Failed to add edge %s -> %s", src_node.fully_qualified_name, dst_node.fully_qualified_name)
    return added


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
