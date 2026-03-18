"""Edge building strategies for call-graph construction.

Two strategies are provided:
- build_edges_via_references: default, used by Python/TS/Go/PHP adapters
- build_edges_via_definitions: used by Java (JDTLS) where references are too slow
"""

from __future__ import annotations

import logging
from pathlib import Path

from static_analyzer.engine.edge_build_context import EdgeBuildContext
from static_analyzer.engine.progress import ProgressLogger
from static_analyzer.constants import NodeType
from static_analyzer.engine.lsp_constants import (
    CALLABLE_KINDS,
    CLASS_LIKE_KINDS,
)
from static_analyzer.engine.models import SymbolInfo
from static_analyzer.engine.protocols import EdgeBuildAdapter
from static_analyzer.engine.symbol_table import SymbolTable
from static_analyzer.engine.utils import uri_to_path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# References-based strategy (default)
# ---------------------------------------------------------------------------


def build_edges_via_references(
    adapter: EdgeBuildAdapter,
    ctx: EdgeBuildContext,
    source_files: list[Path],
) -> set[tuple[str, str]]:
    """Build call-graph edges by querying textDocument/references for each symbol.

    For each trackable symbol, sends batched references queries and filters
    results to actual call sites (invocations, constructor calls, etc.).
    """
    st = ctx.symbol_table

    pos_to_syms, unique_positions = _prepare_trackable_symbols(adapter, st)

    total_unique = len(unique_positions)

    # Group positions by file for progress tracking
    file_positions: dict[str, list[tuple[str, int, int]]] = {}
    for pos_key in unique_positions:
        file_key = pos_key[0]
        file_positions.setdefault(file_key, []).append(pos_key)

    total_files = len(file_positions)
    batch_size = adapter.references_batch_size
    per_query_timeout = adapter.references_per_query_timeout

    edge_set: set[tuple[str, str]] = set()
    refs_total = 0
    refs_call_sites = 0

    skip_files: set[str] = set()
    skipped_positions = 0

    pbar = ProgressLogger("Phase 2 (edges)", total_unique, unit="pos")
    for batch_start in range(0, total_unique, batch_size):
        batch_positions = unique_positions[batch_start : batch_start + batch_size]

        # Filter out positions from files that already produced LSP errors
        filtered_positions: list[tuple[str, int, int]] = []
        for pos_key in batch_positions:
            if pos_key[0] in skip_files:
                skipped_positions += 1
            else:
                filtered_positions.append(pos_key)

        if filtered_positions:
            queries = []
            for pos_key in filtered_positions:
                representative = pos_to_syms[pos_key][0]
                queries.append((representative.file_path, representative.start_line, representative.start_char))

            try:
                result_list, error_indices = ctx.lsp.send_references_batch(queries, per_query_timeout=per_query_timeout)
            except Exception as e:
                logger.warning("Batch references failed: %s", e)
                result_list = [[] for _ in queries]
                error_indices = set()

            for err_idx in error_indices:
                err_file = filtered_positions[err_idx][0]
                if err_file not in skip_files:
                    skip_files.add(err_file)
                    logger.info("Skipping further queries for file with LSP errors: %s", err_file)

            for i, pos_key in enumerate(filtered_positions):
                syms_at_pos = pos_to_syms[pos_key]
                refs = result_list[i] if i < len(result_list) else []

                batch_refs, batch_calls = _process_references_for_position(adapter, ctx, syms_at_pos, refs, edge_set)
                refs_total += batch_refs
                refs_call_sites += batch_calls

        pbar.set_postfix(edges=len(edge_set), files=total_files)
        pbar.update(len(batch_positions))
    pbar.finish()

    if skip_files:
        logger.info(
            "Phase 2: skipped %d positions across %d error-producing files",
            skipped_positions,
            len(skip_files),
        )

    logger.info(
        "Phase 2 (edges): %d/%d references were call sites (%.0f%% filtered out)",
        refs_call_sites,
        refs_total,
        (1 - refs_call_sites / max(refs_total, 1)) * 100,
    )
    return edge_set


def _prepare_trackable_symbols(
    adapter: EdgeBuildAdapter,
    st: SymbolTable,
) -> tuple[dict[tuple[str, int, int], list[SymbolInfo]], list[tuple[str, int, int]]]:
    """Collect trackable symbols and deduplicate by position.

    Returns (pos_to_syms, unique_positions_sorted).
    """
    trackable = sorted(
        [
            sym
            for sym in st.symbols.values()
            if adapter.should_track_for_edges(sym.kind) and not st.is_local_variable(sym)
        ],
        key=lambda s: s.qualified_name,
    )

    pos_to_syms: dict[tuple[str, int, int], list[SymbolInfo]] = {}
    for sym in trackable:
        pos_key = sym.definition_location
        pos_to_syms.setdefault(pos_key, []).append(sym)

    unique_positions = sorted(pos_to_syms.keys())
    total_unique = len(unique_positions)
    total_trackable = len(trackable)
    logger.info(
        "Phase 2 (edges): %d trackable symbols at %d unique positions (%.0f%% dedup)",
        total_trackable,
        total_unique,
        (1 - total_unique / max(total_trackable, 1)) * 100,
    )
    return pos_to_syms, unique_positions


def _process_references_for_position(
    adapter: EdgeBuildAdapter,
    ctx: EdgeBuildContext,
    syms_at_pos: list[SymbolInfo],
    refs: list[dict],
    edge_set: set[tuple[str, str]],
) -> tuple[int, int]:
    """Process reference results for symbols at a single position.

    Filters references to call sites and adds edges to the edge set.
    Returns (total_refs_checked, call_site_refs).
    """
    st = ctx.symbol_table
    si = ctx.source_inspector
    refs_total = 0
    refs_call_sites = 0

    for sym in syms_at_pos:
        sym_def_loc = sym.definition_location
        for ref in refs:
            ref_uri = ref.get("uri", "")
            ref_range = ref.get("range", {})
            ref_start = ref_range.get("start", {})
            ref_end = ref_range.get("end", {})
            ref_line = ref_start.get("line", -1)
            ref_char = ref_start.get("character", -1)
            ref_end_char = ref_end.get("character", -1)

            ref_file = uri_to_path(ref_uri)
            if ref_file is None:
                continue
            ref_loc = (str(ref_file), ref_line, ref_char)
            if ref_loc == sym_def_loc:
                continue

            refs_total += 1

            # Filter to actual call sites based on symbol kind
            if adapter.is_class_like(sym.kind) and not si.is_invocation(ref_file, ref_line, ref_end_char):
                continue
            elif sym.kind == NodeType.CONSTANT and not si.is_invocation(ref_file, ref_line, ref_end_char):
                continue
            elif sym.kind == NodeType.VARIABLE and not si.is_callable_usage(ref_file, ref_line, ref_char, ref_end_char):
                continue

            refs_call_sites += 1

            container = st.find_containing_symbol(ref_file, ref_line, ref_char)
            if not container:
                continue
            container = st.lift_to_callable(container)
            if not container or container.qualified_name == sym.qualified_name:
                continue
            if (str(ref_file), ref_line) == (str(container.file_path), container.start_line):
                continue
            if sym.qualified_name.startswith(container.qualified_name + "."):
                continue
            edge_set.add((container.qualified_name, sym.qualified_name))

    return refs_total, refs_call_sites


# ---------------------------------------------------------------------------
# Definition-based strategy (Java / JDTLS)
# ---------------------------------------------------------------------------


def build_edges_via_definitions(
    adapter: EdgeBuildAdapter,
    ctx: EdgeBuildContext,
    source_files: list[Path],
) -> set[tuple[str, str]]:
    """Build edges via textDocument/definition instead of references.

    JDTLS serializes references requests (~1-10s each), making the default
    references-based approach impractical for large projects. Definition
    queries are ~20ms each, so we scan source for call sites and resolve
    them via definition, then query implementations for polymorphic dispatch.
    """
    st = ctx.symbol_table

    pos_to_sym, line_to_syms = _build_definition_lookups(st)

    edge_set, impl_queries_pending, total_sites, total_resolved = _resolve_definitions(
        adapter, ctx, source_files, pos_to_sym, line_to_syms
    )

    total_impl_resolved = _resolve_implementations(ctx, edge_set, impl_queries_pending, pos_to_sym, line_to_syms)

    logger.info(
        "Phase 2 summary: %d call sites, %d def resolved, %d impl resolved, %d raw edges",
        total_sites,
        total_resolved,
        total_impl_resolved,
        len(edge_set),
    )
    return edge_set


def _build_definition_lookups(
    st: SymbolTable,
) -> tuple[dict[tuple[str, int, int], SymbolInfo], dict[tuple[str, int], list[SymbolInfo]]]:
    """Build position-based lookups for resolving definition results.

    Returns (pos_to_sym, line_to_syms). Prefers the symbol with the longest
    qualified name at each position (e.g. Container.Item.describe() over
    Container.describe()).
    """
    pos_to_sym: dict[tuple[str, int, int], SymbolInfo] = {}
    line_to_syms: dict[tuple[str, int], list[SymbolInfo]] = {}
    for sym in st.symbols.values():
        pos = sym.definition_location
        existing = pos_to_sym.get(pos)
        if existing is None or len(sym.qualified_name) > len(existing.qualified_name):
            pos_to_sym[pos] = sym
        key = (str(sym.file_path), sym.start_line)
        line_to_syms.setdefault(key, []).append(sym)
    return pos_to_sym, line_to_syms


def _resolve_definitions(
    adapter: EdgeBuildAdapter,
    ctx: EdgeBuildContext,
    source_files: list[Path],
    pos_to_sym: dict[tuple[str, int, int], SymbolInfo],
    line_to_syms: dict[tuple[str, int], list[SymbolInfo]],
) -> tuple[set[tuple[str, str]], list[tuple[str, Path, int, int]], int, int]:
    """Phase 2a: Resolve call sites via textDocument/definition.

    Returns (edge_set, impl_queries_pending, total_sites, total_resolved).
    """
    edge_set: set[tuple[str, str]] = set()
    st = ctx.symbol_table
    total_files = len(source_files)
    total_sites = 0
    total_resolved = 0
    batch_size = 50
    impl_queries_pending: list[tuple[str, Path, int, int]] = []

    pbar = ProgressLogger("Phase 2 (definitions)", total_files, unit="file")
    for file_path in source_files:
        call_sites = ctx.source_inspector.find_call_sites(file_path)
        if not call_sites:
            pbar.update(1)
            continue

        total_sites += len(call_sites)

        for batch_start in range(0, len(call_sites), batch_size):
            batch = call_sites[batch_start : batch_start + batch_size]
            queries = [(file_path, line, col) for line, col in batch]

            try:
                results, _ = ctx.lsp.send_definition_batch(queries)
            except Exception as e:
                logger.warning("Definition batch failed for %s: %s", file_path.name, e)
                continue

            for i, (site_line, site_col) in enumerate(batch):
                defs = results[i] if i < len(results) else []
                if not defs:
                    continue

                caller = st.find_containing_symbol(file_path, site_line, site_col)
                if not caller:
                    continue
                caller = st.lift_to_callable(caller)
                if not caller:
                    continue

                for def_result in defs:
                    target = _resolve_definition_to_symbol(def_result, pos_to_sym, line_to_syms)
                    if not target:
                        continue
                    total_resolved += 1

                    if not _is_valid_edge(caller, target):
                        continue

                    edge_set.add((caller.qualified_name, target.qualified_name))

                    # If target is a callable with a class-like parent, also add edge to the parent class
                    if adapter.is_callable(target.kind) and target.parent_chain:
                        _, parent_kind = target.parent_chain[-1]
                        if adapter.is_class_like(parent_kind):
                            parent_qname = target.qualified_name.rsplit(".", 1)[0]
                            paren_idx = parent_qname.find("(")
                            if paren_idx != -1:
                                parent_qname = parent_qname[:paren_idx]
                            if parent_qname in st.symbols:
                                parent_sym = st.symbols[parent_qname]
                                if _is_valid_edge(caller, parent_sym):
                                    edge_set.add((caller.qualified_name, parent_qname))

                    # Queue implementation query for polymorphic dispatch
                    if adapter.is_callable(target.kind):
                        impl_queries_pending.append(
                            (
                                caller.qualified_name,
                                target.file_path,
                                target.start_line,
                                target.start_char,
                            )
                        )

        pbar.set_postfix(edges=len(edge_set), resolved=total_resolved)
        pbar.update(1)
    pbar.finish()

    return edge_set, impl_queries_pending, total_sites, total_resolved


def _resolve_implementations(
    ctx: EdgeBuildContext,
    edge_set: set[tuple[str, str]],
    impl_queries_pending: list[tuple[str, Path, int, int]],
    pos_to_sym: dict[tuple[str, int, int], SymbolInfo],
    line_to_syms: dict[tuple[str, int], list[SymbolInfo]],
) -> int:
    """Phase 2b: Resolve implementations for polymorphic call targets.

    Adds implementation edges to edge_set in-place. Returns total_impl_resolved.
    """
    st = ctx.symbol_table
    batch_size = 50

    target_pos_to_callers: dict[tuple[str, int, int], set[str]] = {}
    for caller_qname, tgt_file, tgt_line, tgt_char in impl_queries_pending:
        tgt_key = (str(tgt_file), tgt_line, tgt_char)
        target_pos_to_callers.setdefault(tgt_key, set()).add(caller_qname)

    unique_impl_targets = list(target_pos_to_callers.keys())
    total_impl_queries = len(unique_impl_targets)
    logger.info(
        "Phase 2b (implementations): %d unique targets from %d pending queries",
        total_impl_queries,
        len(impl_queries_pending),
    )

    total_impl_resolved = 0

    pbar = ProgressLogger("Phase 2b (impl)", total_impl_queries, unit="target")
    for batch_start in range(0, len(unique_impl_targets), batch_size):
        batch_keys = unique_impl_targets[batch_start : batch_start + batch_size]
        queries = [(Path(fk), ln, ch) for fk, ln, ch in batch_keys]

        try:
            impl_results, _ = ctx.lsp.send_implementation_batch(queries)
        except Exception as e:
            logger.warning("Implementation batch failed: %s", e)
            pbar.update(len(batch_keys))
            continue

        for j, tgt_key in enumerate(batch_keys):
            impls = impl_results[j] if j < len(impl_results) else []
            callers = target_pos_to_callers[tgt_key]

            for impl_result in impls:
                impl_sym = _resolve_definition_to_symbol(impl_result, pos_to_sym, line_to_syms)
                if not impl_sym:
                    continue
                total_impl_resolved += 1

                for caller_qname in callers:
                    caller_sym = st.symbols.get(caller_qname)
                    if caller_sym and _is_valid_edge(caller_sym, impl_sym):
                        edge_set.add((caller_qname, impl_sym.qualified_name))

        pbar.set_postfix(edges=len(edge_set), resolved=total_impl_resolved)
        pbar.update(len(batch_keys))
    pbar.finish()

    return total_impl_resolved


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _is_valid_edge(caller: SymbolInfo, target: SymbolInfo) -> bool:
    """Check if an edge between caller and target is valid."""
    if target.qualified_name == caller.qualified_name:
        return False
    if target.qualified_name.startswith(caller.qualified_name + "."):
        return False
    if caller.qualified_name.startswith(target.qualified_name + "."):
        return False
    if target.definition_location == caller.definition_location:
        return False
    if (str(target.file_path), target.start_line) == (str(caller.file_path), caller.start_line):
        return False
    return True


def _resolve_definition_to_symbol(
    def_result: dict,
    pos_to_sym: dict[tuple[str, int, int], SymbolInfo],
    line_to_syms: dict[tuple[str, int], list[SymbolInfo]],
) -> SymbolInfo | None:
    """Resolve a definition LSP result to a SymbolInfo in our table."""
    if "targetUri" in def_result:
        uri = def_result["targetUri"]
        sel_range = def_result.get("targetSelectionRange", def_result.get("targetRange", {}))
    else:
        uri = def_result.get("uri", "")
        sel_range = def_result.get("range", {})

    file_path = uri_to_path(uri)
    if file_path is None:
        return None

    start = sel_range.get("start", {})
    line = start.get("line", -1)
    char = start.get("character", -1)
    file_key = str(file_path)

    # Exact match on (file, line, char)
    sym = pos_to_sym.get((file_key, line, char))
    if sym:
        return sym

    # Fuzzy: match on (file, line) — prefer callable > class > other, longest name wins
    candidates = line_to_syms.get((file_key, line), [])
    if candidates:
        best = _best_candidate(candidates)
        if best:
            return best

    # Try adjacent lines (definition range start vs selectionRange start)
    for delta in (1, -1, 2, -2):
        candidates = line_to_syms.get((file_key, line + delta), [])
        if candidates:
            best = _best_candidate(candidates)
            if best:
                return best
    return None


def _best_candidate(candidates: list[SymbolInfo]) -> SymbolInfo | None:
    """Pick the best symbol from candidates: callable > class > other, longest name wins."""
    callables = [c for c in candidates if c.kind in CALLABLE_KINDS]
    if callables:
        return max(callables, key=lambda c: len(c.qualified_name))
    classes = [c for c in candidates if c.kind in CLASS_LIKE_KINDS]
    if classes:
        return max(classes, key=lambda c: len(c.qualified_name))
    return max(candidates, key=lambda c: len(c.qualified_name)) if candidates else None
