"""Pkl warm-start updater: bring cached per-language analysis up to date.

Warm-start flow:
1. Keep unchanged files from the pkl and invalidate changed/deleted files.
2. Re-LSP existing changed files and merge their fresh nodes/references back in.
3. Restore cached cross-boundary edges only when live references still prove them.
4. Add new outbound edges by resolving changed-file call sites with definitions.
5. Keep unchanged-only edges cached and let ``StaticAnalyzer`` persist the new pkl.
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
from static_analyzer.config import NodeType
from static_analyzer.engine.call_graph_builder import CallGraphBuilder
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.lsp_constants import EdgeStrategy
from static_analyzer.engine.result_converter import convert_to_codeboarding_format
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.utils import definition_location, uri_to_path
from static_analyzer.cfg import CallGraph, EdgeKind
from static_analyzer.internal_references import is_self_or_container_edge, parent_qualified_name
from static_analyzer.node import Node

logger = logging.getLogger(__name__)

_DEFINITION_BATCH_SIZE = 50


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
        # Rebuilding only the changed files hides any unchanged file that claims the same
        # qualified name, so the contest scan would drop the origin from this side while the
        # cached side keeps it. Replay what the baseline already chose.
        builder.symbol_table.adopt_baseline_names(_baseline_names_for(cached_analysis, changed_source_files))
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
    _rebuild_changed_file_edges(
        merged_analysis,
        updated_cache.invalidated_edges,
        updated_cache.invalidated_files,
        changed_source_files,
        adapter,
        engine_client,
    )
    return _filter_to_live_files(merged_analysis).to_dict()


def _rebuild_changed_file_edges(
    merged_analysis: AnalysisData,
    invalidated_edges: list[InvalidatedEdge],
    changed_file_strs: set[str],
    changed_source_files: list[Path],
    adapter: LanguageAdapter,
    engine_client: LSPClient,
) -> None:
    source_inspector = SourceInspector()
    _restore_cross_boundary_edges(
        merged_analysis.call_graph, invalidated_edges, changed_file_strs, adapter, engine_client, source_inspector
    )
    _add_outbound_edges_from_changed_files(
        merged_analysis.call_graph,
        changed_source_files,
        engine_client,
        source_inspector,
        adapter,
    )


def _restore_cross_boundary_edges(
    call_graph: CallGraph,
    invalidated_edges: list[InvalidatedEdge],
    changed_file_strs: set[str],
    adapter: LanguageAdapter,
    engine_client: LSPClient,
    source_inspector: SourceInspector,
) -> None:
    if not invalidated_edges:
        return

    if adapter.edge_strategy == EdgeStrategy.DEFINITIONS:
        _restore_inbound_edges_via_definitions(
            call_graph,
            invalidated_edges,
            changed_file_strs,
            adapter,
            engine_client,
            include_callable_parent=True,
        )
        return

    checked = {"inbound": 0, "outbound": 0}
    restored = {"inbound": 0, "outbound": 0}
    references_cache: dict[str, list[dict]] = {}

    for src_name, dst_name, old_src_node, old_dst_node, _cached_sites in invalidated_edges:
        src_changed = old_src_node.file_path in changed_file_strs
        dst_changed = old_dst_node.file_path in changed_file_strs
        if src_changed == dst_changed:
            continue
        if not call_graph.has_node(src_name) or not call_graph.has_node(dst_name):
            continue

        src_node = call_graph.nodes[src_name]
        dst_node = call_graph.nodes[dst_name]
        direction = "outbound" if src_changed else "inbound"

        checked[direction] += 1
        refs = references_cache.get(dst_name)
        if refs is None:
            try:
                dst_path = Path(dst_node.file_path)
                engine_client.did_open(dst_path)
                refs = engine_client.references(Path(dst_node.file_path), dst_node.line_start - 1, dst_node.col_start)
            except Exception as exc:
                # An empty result here means the cached edge is not re-confirmed and
                # is therefore deleted, which is a silent loss rather than a retry.
                logger.debug("Failed to validate references for %s", dst_name, exc_info=True)
                refs = []
            references_cache[dst_name] = refs

        call_sites = _edge_reference_call_sites(src_node, dst_node, refs, adapter, source_inspector)
        if call_sites:
            try:
                call_graph.add_edge(src_name, dst_name, call_sites=call_sites)
                restored[direction] += 1
            except ValueError:
                logger.debug("Failed to restore edge %s -> %s", src_name, dst_name, exc_info=True)

    logger.info(
        "Validated cached cross-boundary edges, restored inbound %d/%d and outbound %d/%d",
        restored["inbound"],
        checked["inbound"],
        restored["outbound"],
        checked["outbound"],
    )


def _restore_inbound_edges_via_definitions(
    call_graph: CallGraph,
    invalidated_edges: list[InvalidatedEdge],
    changed_file_strs: set[str],
    adapter: LanguageAdapter,
    engine_client: LSPClient,
    include_callable_parent: bool,
) -> None:
    """Re-resolve invalidated edges whose source file is unchanged, from their cached call sites.

    Why not references: an adapter on the definitions strategy is there because
    its server answers references far too slowly to sit on this path, and the
    hot symbols an edit invalidates are its worst cases. The cached call sites
    let us ask the same question with a definition query instead: does this
    position still resolve to that destination? Restoring without asking is not
    enough, an unchanged caller can still lose the edge when the destination's
    own declaration moves out from under it.

    The outbound direction is absent here on purpose:
    ``_add_outbound_edges_from_changed_files`` re-resolves those from live source.
    """
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

    queries: list[tuple[Path, int, int]] = []
    lookup: list[tuple[tuple[str, str], dict[str, str | int]]] = []
    for edge, sites in pending.items():
        for site in sites:
            queries.append((Path(str(site["file"])), int(site["line"]) - 1, int(site["column"]) - 1))
            lookup.append((edge, site))

    confirmed: dict[tuple[str, str], list[dict[str, str | int]]] = {}
    for start in range(0, len(queries), _DEFINITION_BATCH_SIZE):
        batch = lookup[start : start + _DEFINITION_BATCH_SIZE]
        try:
            definition_results, _ = engine_client.send_definition_batch(queries[start : start + _DEFINITION_BATCH_SIZE])
        except Exception:
            logger.debug("Definition batch failed while restoring cached edges", exc_info=True)
            continue
        for (edge, site), definitions in zip(batch, definition_results):
            # A polymorphic call resolves to the interface or base declaration, never
            # to the implementation the cached edge names, so exact equality alone
            # drops every caller-to-implementation edge whose implementation file
            # changed -- silently, while the update reports success.
            resolved = [
                dst_node
                for definition in definitions
                for dst_node in _definition_nodes(call_graph, definition, include_callable_parent)
            ]
            reachable = {node.fully_qualified_name for node in resolved}
            if adapter.expands_virtual_dispatch:
                for node in resolved:
                    reachable.update(o.fully_qualified_name for o in _override_nodes(call_graph, node))
            if edge[1] in reachable:
                confirmed.setdefault(edge, []).append(site)

    restored = 0
    for (src_name, dst_name), sites in confirmed.items():
        try:
            call_graph.add_edge(src_name, dst_name, call_sites=sites)
            restored += 1
        except ValueError:
            logger.debug("Failed to restore edge %s -> %s", src_name, dst_name, exc_info=True)

    logger.info("Restored %d of %d cached inbound edge(s) via definitions", restored, len(pending))


def _edge_reference_call_sites(
    src_node: Node,
    dst_node: Node,
    refs: list[dict],
    adapter: LanguageAdapter,
    source_inspector: SourceInspector,
) -> list[dict[str, str | int]]:
    call_sites: list[dict[str, str | int]] = []
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
        call_sites.append({"file": str(ref_file), "line": ref_line + 1, "column": ref_char + 1})
    return call_sites


def _members_named(call_graph: CallGraph, owner: Node, name: str) -> list[Node]:
    """Nodes for ``owner``'s members called *name*, by qualified-name prefix.

    The full rebuild reads these off the symbol table; incrementally only the
    merged graph is available, so the owning type's members are found by name.
    """
    prefix = f"{owner.fully_qualified_name}.{name}"
    return [node for qname, node in call_graph.nodes.items() if qname == prefix or qname.startswith(f"{prefix}(")]


def _override_nodes(call_graph: CallGraph, target: Node) -> list[Node]:
    """Same-named members on types that inherit the target's owner.

    A call through a base-typed reference resolves to the base declaration, so
    without this the incremental graph stops where the full rebuild continues.
    The full rebuild builds a subclass index from source; here the INHERITS
    edges already in the merged graph carry the same relation.
    """
    if not target.is_callable():
        return []
    owner_qname = parent_qualified_name(target.fully_qualified_name)
    owner = call_graph.nodes.get(owner_qname)
    if owner is None:
        return []
    member = target.fully_qualified_name[len(owner_qname) + 1 :].split("(")[0]
    overrides: list[Node] = []
    for ref in call_graph.reference_edges:
        if ref.kind is EdgeKind.INHERITS and ref.dst == owner_qname:
            derived = call_graph.nodes.get(ref.src)
            if derived is not None:
                overrides.extend(_members_named(call_graph, derived, member))
    return overrides


def _add_outbound_edges_from_changed_files(
    call_graph: CallGraph,
    changed_source_files: list[Path],
    engine_client: LSPClient,
    source_inspector: SourceInspector,
    adapter: LanguageAdapter,
) -> None:
    if not changed_source_files:
        return

    include_callable_parent = adapter.edge_strategy == EdgeStrategy.DEFINITIONS
    added = 0
    changed_file_strs = {str(file_path) for file_path in changed_source_files}

    for file_path in changed_source_files:
        call_sites = source_inspector.find_call_sites(file_path)
        method_group_positions: set[tuple[int, int]] = set()
        if adapter.resolves_method_groups:
            known = {(site.lsp_line, site.lsp_column) for site in call_sites}
            for site in source_inspector.find_method_group_sites(file_path):
                if (site.lsp_line, site.lsp_column) in known:
                    continue
                method_group_positions.add((site.lsp_line, site.lsp_column))
                call_sites.append(site)
        collection_positions: set[tuple[int, int]] = set()
        if adapter.resolves_collection_initializers:
            collection_positions = {
                (site.lsp_line, site.lsp_column)
                for site in source_inspector.find_collection_initializer_sites(file_path)
            }
        added += _add_iterated_type_edges(
            call_graph,
            file_path,
            engine_client,
            source_inspector,
            adapter,
            changed_file_strs,
        )
        if not call_sites:
            continue
        queries = [(file_path, site.lsp_line, site.lsp_column) for site in call_sites]
        try:
            definition_results, _ = engine_client.send_definition_batch(queries)
        except Exception:
            logger.debug("Failed to resolve outbound definitions for %s", file_path, exc_info=True)
            continue

        for site, definitions in zip(call_sites, definition_results):
            line = site.lsp_line
            char = site.lsp_column
            src_node = _containing_source_node(call_graph, file_path, line, char)
            if src_node is None:
                continue
            is_method_group = (line, char) in method_group_positions
            for definition in definitions:
                for resolved in _definition_nodes(call_graph, definition, include_callable_parent):
                    # Same rule as the full rebuild: an argument position is a
                    # method group only when it resolves to something callable.
                    if is_method_group and not (resolved.is_callable() or resolved.is_class()):
                        continue
                    targets = [resolved]
                    if adapter.expands_virtual_dispatch:
                        targets += _override_nodes(call_graph, resolved)
                    if (line, char) in collection_positions:
                        targets += _members_named(call_graph, resolved, "Add")
                    for dst_node in targets:
                        # The fresh partial analysis already owns changed-to-changed edges.
                        if dst_node.file_path in changed_file_strs:
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
                            logger.debug(
                                "Failed to add outbound edge %s -> %s",
                                src_node.fully_qualified_name,
                                dst_node.fully_qualified_name,
                                exc_info=True,
                            )

    if added:
        logger.info("Added %d new outbound edge(s) from changed files", added)


def _add_iterated_type_edges(
    call_graph: CallGraph,
    file_path: Path,
    engine_client: LSPClient,
    source_inspector: SourceInspector,
    adapter: LanguageAdapter,
    changed_file_strs: set[str],
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
    try:
        results, _ = engine_client.send_type_definition_batch(
            [(file_path, site.lsp_line, site.lsp_column) for site in sites]
        )
    except Exception:
        logger.warning("Failed to resolve iterated types for %s", file_path, exc_info=True)
        return 0

    added = 0
    for site, definitions in zip(sites, results):
        src_node = _containing_source_node(call_graph, file_path, site.lsp_line, site.lsp_column)
        if src_node is None:
            continue
        for definition in definitions:
            for type_node in _definition_nodes(call_graph, definition, include_callable_parent=False):
                for dst_node in [type_node] + _members_named(call_graph, type_node, "GetEnumerator"):
                    if dst_node.file_path in changed_file_strs:
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
                        logger.debug("Failed to add iterated-type edge", exc_info=True)
    return added


def _containing_source_node(call_graph: CallGraph, file_path: Path, line: int, char: int) -> Node | None:
    """The node an edge from this position belongs to, callable or otherwise.

    A field initializer -- ``private readonly Store _store = new Store();`` -- calls
    a constructor from outside any method, so a callable-only lookup finds nothing
    and the edge is dropped. The full rebuild attributes it to the enclosing class,
    and an incremental run has to agree or it loses the edge on every edit to that
    file, including edits that change nothing.
    """
    callable_node = _most_specific_node_at_position(call_graph, file_path, line, char, callable_only=True)
    if callable_node is not None:
        return callable_node
    return _most_specific_node_at_position(call_graph, file_path, line, char)


def _most_specific_node_at_position(
    call_graph: CallGraph,
    file_path: Path,
    line: int,
    char: int,
    callable_only: bool = False,
) -> Node | None:
    matches = [
        node
        for node in call_graph.nodes.values()
        if node.file_path == str(file_path)
        and (not callable_only or node.is_callable())
        and _position_inside_node(node, line, char)
    ]
    if not matches:
        return None
    return max(
        matches,
        key=lambda node: (
            node.line_start,
            node.col_start,
            -node.line_end,
            len(node.fully_qualified_name),
        ),
    )


def _definition_nodes(
    call_graph: CallGraph,
    definition: dict[str, Any],
    include_callable_parent: bool = False,
) -> list[Node]:
    location = definition_location(definition)
    if location is None:
        return []
    file_path, line, character = location
    target = _most_specific_node_at_position(call_graph, file_path, line, character)
    if target is None:
        same_line = [
            node
            for node in call_graph.nodes.values()
            if node.file_path == str(file_path) and node.line_start == line + 1
        ]
        if same_line:
            target = max(
                same_line,
                key=lambda node: (
                    node.is_class(),
                    node.is_callable(),
                    len(node.fully_qualified_name),
                ),
            )
    if target is None:
        return []

    targets = [target]
    if (include_callable_parent and target.is_callable()) or target.type == NodeType.CONSTRUCTOR:
        parent = call_graph.nodes.get(parent_qualified_name(target.fully_qualified_name))
        if parent is not None and parent.is_class():
            targets.append(parent)
    return targets


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


def _baseline_names_for(cached_analysis: dict, files: list[Path]) -> dict[Path, set[str]]:
    """What the cached graph calls the symbols declared in each of *files*."""
    graph = cached_analysis.get("call_graph")
    if graph is None:
        return {}
    wanted = {str(f): f for f in files}
    names: dict[Path, set[str]] = {}
    for name, node in graph.nodes.items():
        source = wanted.get(str(node.file_path))
        if source is not None:
            names.setdefault(source, set()).add(name)
    return names
