"""Pkl warm-start updater: bring a cached per-language analysis up to date in-memory.

Given a cached analysis dict (loaded from the SHA-tagged pkl) and the file
list git reports as changed since the pkl's tag SHA, rerun the LSP for those
files only and merge the result with the kept-from-cache state. No JSON, no
disk writes — the only persistence is the pkl save that ``StaticAnalyzer``
performs after this returns.
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
from static_analyzer.constants import NodeType
from static_analyzer.engine.call_graph_builder import CallGraphBuilder
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.result_converter import convert_to_codeboarding_format
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.utils import uri_to_path
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node

logger = logging.getLogger(__name__)


def update_cfg_for_changed_files(
    cached_analysis: dict[str, Any],
    changed_files: set[Path],
    adapter: LanguageAdapter,
    project_path: Path,
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
        builder = CallGraphBuilder(engine_client, adapter, project_path)
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
    _restore_persisted_cross_boundary_edges(
        merged_analysis,
        updated_cache.invalidated_edges,
        updated_cache.invalidated_files,
        adapter,
        engine_client,
    )
    _add_new_outbound_edges_from_changed_files(merged_analysis, changed_source_files, engine_client)
    return _filter_to_live_files(merged_analysis).to_dict()


def _restore_persisted_cross_boundary_edges(
    merged_analysis: AnalysisData,
    candidate_edges: list[InvalidatedEdge],
    changed_file_strs: set[str],
    adapter: LanguageAdapter,
    engine_client: LSPClient,
) -> None:
    if not candidate_edges:
        return

    call_graph = merged_analysis.call_graph
    source_inspector = SourceInspector()
    restored = 0
    checked = 0
    references_cache: dict[str, list[dict]] = {}
    definitions_cache: dict[str, list[list[dict]]] = {}

    for src_name, dst_name, _old_src_node, _old_dst_node in candidate_edges:
        if not call_graph.has_node(src_name) or not call_graph.has_node(dst_name):
            continue

        src_node = call_graph.nodes.get(src_name)
        dst_node = call_graph.nodes.get(dst_name)
        if src_node is None or dst_node is None:
            continue

        checked += 1
        refs = references_cache.get(dst_name)
        if refs is None:
            try:
                engine_client.did_open(Path(dst_node.file_path), adapter.language_id)
                refs = engine_client.references(Path(dst_node.file_path), dst_node.line_start - 1, dst_node.col_start)
            except Exception:
                logger.debug("Failed to validate references for %s", dst_name, exc_info=True)
                refs = []
            references_cache[dst_name] = refs

        # Inbound: unchanged B -> changed A is restored by asking "who references A?"
        # and checking whether one reference still lands inside B.
        edge_exists = _edge_reference_still_exists(src_node, dst_node, refs, adapter, source_inspector)
        if not edge_exists and src_node.file_path in changed_file_strs:
            # Outbound: changed A -> unchanged C falls back to definitions from A's call sites.
            edge_exists = _outbound_definition_edge_still_exists(
                src_node, dst_node, definitions_cache, engine_client, source_inspector
            )

        if edge_exists:
            try:
                call_graph.add_edge(src_name, dst_name)
                restored += 1
            except ValueError:
                logger.debug("Failed to restore edge %s -> %s", src_name, dst_name, exc_info=True)

    logger.info(
        "Validated %d cross-boundary cached edge(s), restored %d/%d",
        len(candidate_edges),
        restored,
        checked,
    )


def _edge_reference_still_exists(
    src_node: Node,
    dst_node: Node,
    refs: list[dict],
    adapter: LanguageAdapter,
    source_inspector: SourceInspector,
) -> bool:
    for ref in refs:
        ref_file = uri_to_path(ref.get("uri", ""))
        if ref_file is None or str(ref_file) != src_node.file_path:
            continue

        ref_range = ref.get("range", {})
        ref_start = ref_range.get("start", {})
        ref_end = ref_range.get("end", {})
        ref_line = ref_start.get("line", -1)
        ref_char = ref_start.get("character", -1)
        ref_end_char = ref_end.get("character", -1)
        if not _position_inside_node(src_node, ref_line, ref_char):
            continue
        if not _reference_matches_edge_kind(
            dst_node, ref_file, ref_line, ref_char, ref_end_char, adapter, source_inspector
        ):
            continue
        return True
    return False


def _outbound_definition_edge_still_exists(
    src_node: Node,
    dst_node: Node,
    definitions_cache: dict[str, list[list[dict]]],
    engine_client: LSPClient,
    source_inspector: SourceInspector,
) -> bool:
    definition_results = definitions_cache.get(src_node.fully_qualified_name)
    if definition_results is None:
        file_path = Path(src_node.file_path)
        call_sites = [
            (file_path, line, char)
            for line, char in source_inspector.find_call_sites(file_path)
            if _position_inside_node(src_node, line, char)
        ]
        if not call_sites:
            definition_results = []
        else:
            try:
                definition_results, _ = engine_client.send_definition_batch(call_sites)
            except Exception:
                logger.debug(
                    "Failed to validate outbound definitions for %s", src_node.fully_qualified_name, exc_info=True
                )
                definition_results = []
        definitions_cache[src_node.fully_qualified_name] = definition_results

    for definitions in definition_results:
        for definition in definitions:
            if _definition_points_to_node(definition, dst_node):
                return True
    return False


def _add_new_outbound_edges_from_changed_files(
    merged_analysis: AnalysisData,
    changed_source_files: list[Path],
    engine_client: LSPClient,
) -> None:
    if not changed_source_files:
        return

    call_graph = merged_analysis.call_graph
    source_inspector = SourceInspector()
    added = 0

    for file_path in changed_source_files:
        call_sites = source_inspector.find_call_sites(file_path)
        if not call_sites:
            continue
        queries = [(file_path, line, char) for line, char in call_sites]
        try:
            definition_results, _ = engine_client.send_definition_batch(queries)
        except Exception:
            logger.debug("Failed to resolve outbound definitions for %s", file_path, exc_info=True)
            continue

        for (line, char), definitions in zip(call_sites, definition_results):
            src_node = _find_containing_callable_node(call_graph, file_path, line, char)
            if src_node is None:
                continue
            for definition in definitions:
                dst_node = _find_definition_node(call_graph, definition)
                if dst_node is None or dst_node.fully_qualified_name == src_node.fully_qualified_name:
                    continue
                try:
                    call_graph.add_edge(src_node.fully_qualified_name, dst_node.fully_qualified_name)
                    added += 1
                except ValueError:
                    logger.debug(
                        "Failed to add outbound edge %s -> %s",
                        src_node.fully_qualified_name,
                        dst_node.fully_qualified_name,
                        exc_info=True,
                    )

    if added:
        logger.info("Added %d new outbound edge(s) from changed files", added)


def _find_containing_callable_node(call_graph: CallGraph, file_path: Path, line: int, char: int) -> Node | None:
    containing = [
        node
        for node in call_graph.nodes.values()
        if node.is_callable() and node.file_path == str(file_path) and _position_inside_node(node, line, char)
    ]
    if not containing:
        return None
    return max(containing, key=lambda node: (node.line_start, node.col_start, len(node.fully_qualified_name)))


def _find_definition_node(call_graph: CallGraph, definition: dict) -> Node | None:
    for node in call_graph.nodes.values():
        if _definition_points_to_node(definition, node):
            return node
    return None


def _definition_points_to_node(definition: dict, dst_node: Node) -> bool:
    uri = definition.get("targetUri", definition.get("uri", ""))
    file_path = uri_to_path(uri)
    if file_path is None or str(file_path) != dst_node.file_path:
        return False
    selection_range = definition.get("targetSelectionRange", definition.get("targetRange", definition.get("range", {})))
    start = selection_range.get("start", {})
    line = start.get("line", -1)
    character = start.get("character", -1)
    return _position_inside_node(dst_node, line, character)


def _position_inside_node(node: Node, zero_based_line: int, character: int) -> bool:
    line = zero_based_line + 1
    if line < node.line_start or line > node.line_end:
        return False
    if line == node.line_start and character < node.col_start:
        return False
    return True


def _reference_matches_edge_kind(
    dst_node: Node,
    ref_file: Path,
    ref_line: int,
    ref_char: int,
    ref_end_char: int,
    adapter: LanguageAdapter,
    source_inspector: SourceInspector,
) -> bool:
    if adapter.is_class_like(dst_node.type) and not source_inspector.is_invocation(ref_file, ref_line, ref_end_char):
        return False
    if dst_node.type == NodeType.CONSTANT and not source_inspector.is_invocation(ref_file, ref_line, ref_end_char):
        return False
    if dst_node.type == NodeType.VARIABLE and not source_inspector.is_callable_usage(
        ref_file, ref_line, ref_char, ref_end_char
    ):
        return False
    return True


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

    merged_analysis.call_graph = merged_analysis.call_graph.filter(
        lambda node: node.file_path in existing_file_strs,
        on_dropped_edge=lambda _edge: None,
    )

    merged_analysis.class_hierarchies = {
        name: info
        for name, info in merged_analysis.class_hierarchies.items()
        if info.get("file_path") in existing_file_strs
    }

    filtered_packages: dict[str, Any] = {}
    for pkg_name, pkg_info in merged_analysis.package_relations.items():
        existing_pkg_files = [f for f in pkg_info.get("files", []) if f in existing_file_strs]
        if existing_pkg_files:
            filtered_packages[pkg_name] = {**pkg_info, "files": existing_pkg_files}
    merged_analysis.package_relations = filtered_packages

    return merged_analysis
