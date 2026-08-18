"""Edge building strategies for call-graph construction.

Two strategies are provided:
- build_edges_via_references: default, used by Python/TS/Go/PHP adapters
- build_edges_via_definitions: used by Java (JDTLS) where references are too slow
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from static_analyzer.engine.edge_build_context import EdgeBuildContext
from static_analyzer.exceptions import EdgeResolutionError
from telemetry.degradations import record as record_degradation
from telemetry.events import capture_error
from static_analyzer.engine.progress import ProgressLogger
from static_analyzer.constants import NodeType
from static_analyzer.engine.lsp_constants import (
    CALLABLE_KINDS,
    CLASS_LIKE_KINDS,
)
from static_analyzer.engine.models import CallSite, SymbolInfo
from static_analyzer.engine.protocols import EdgeBuildAdapter
from static_analyzer.engine.symbol_table import SymbolTable
from static_analyzer.engine.utils import definition_location, uri_to_path
from static_analyzer.internal_references import is_self_or_container_edge, parent_qualified_name

logger = logging.getLogger(__name__)

EdgeMap = dict[tuple[str, str], list[CallSite]]


@dataclass(frozen=True)
class ImplementationQuery:
    caller_qname: str
    target_file: Path
    target_line: int
    target_char: int
    call_site: CallSite


@dataclass(frozen=True)
class DefinitionResolution:
    edge_set: EdgeMap
    impl_queries_pending: list[ImplementationQuery]
    total_sites: int
    total_resolved: int


# Modifiers that mean a derived member does NOT take part in dispatch through
# the base: ``new`` hides, ``static`` and ``private`` cannot be reached at all.
_NON_DISPATCHING_MODIFIERS = frozenset({"new", "static", "private"})


@dataclass(frozen=True)
class DispatchIndex:
    """What source says about inheritance, for servers that answer no hierarchy query."""

    subclasses: dict[str, list[SymbolInfo]]
    modifiers: dict[tuple[str, str], frozenset[str]]
    ambiguous: set[str]

    def dispatches_to(self, owner: SymbolInfo, subclass_name: str, member: str) -> bool:
        """Whether a call on *owner* can actually land on *subclass_name*'s *member*."""
        if owner.kind == NodeType.INTERFACE:
            # Implicit implementations carry no modifier at all, so the only
            # thing to exclude is a member that is not an implementation.
            return not (self.modifiers.get((subclass_name, member), frozenset()) & _NON_DISPATCHING_MODIFIERS)
        found = self.modifiers.get((subclass_name, member))
        if found is None:
            return True
        return "override" in found or "explicit" in found


# ---------------------------------------------------------------------------
# References-based strategy (default)
# ---------------------------------------------------------------------------


def build_edges_via_references(
    adapter: EdgeBuildAdapter,
    ctx: EdgeBuildContext,
    source_files: list[Path],
) -> EdgeMap:
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

    edge_set: EdgeMap = {}
    refs_total = 0
    refs_call_sites = 0

    skip_files: set[str] = set()
    skipped_positions = 0

    pbar = ProgressLogger("Phase 2 (edges)", total_unique, unit="pos")
    batch_start = 0
    while batch_start < total_unique:
        # The recycler samples the server here — it decides both whether to
        # restart it and how many queries this batch may carry.
        size = batch_size if ctx.recycler is None else ctx.recycler.before_batch(batch_size)
        batch_positions = unique_positions[batch_start : batch_start + size]
        batch_start += len(batch_positions)

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
                # Every position in this batch loses its edges. The references path
                # is what Python/TS/Go/PHP run, so this is not a C#-only blind spot.
                logger.warning("Batch references failed (%d positions): %s", len(queries), e)
                record_degradation("references_batch", f"{type(e).__name__}: {e}", items=len(queries))
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
    if ctx.recycler is not None and ctx.recycler.recycle_count:
        # Worth one line at the end: a recycled run answered some queries from a
        # different server process than the one it started with.
        logger.info(
            "Phase 2 (edges): the language server was recycled %d time(s); %d batches ran shrunk under memory pressure",
            ctx.recycler.recycle_count,
            ctx.recycler.shrunk_batches,
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
    edge_set: EdgeMap,
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
            if ref_loc == container.definition_location:
                continue
            is_declaration_line = (str(ref_file), ref_line) == (
                str(container.file_path),
                container.start_line,
            )
            is_declaration_body = is_declaration_line and si.is_reference_in_declaration_body(
                ref_file,
                container.start_line,
                container.start_char,
                ref_line,
                ref_char,
                ref_end_char,
                include_expression_body=adapter.include_references_on_declaration_line,
            )
            if is_declaration_line:
                if not is_declaration_body:
                    continue
                if adapter.is_callable(sym.kind) and not si.is_invocation(ref_file, ref_line, ref_end_char):
                    continue
            if sym.qualified_name.startswith(container.qualified_name + "."):
                continue
            _add_edge_site(edge_set, container.qualified_name, sym.qualified_name, ref_file, ref_line, ref_char)

    return refs_total, refs_call_sites


# ---------------------------------------------------------------------------
# Definition-based strategy (Java / JDTLS)
# ---------------------------------------------------------------------------


def build_edges_via_definitions(
    adapter: EdgeBuildAdapter,
    ctx: EdgeBuildContext,
    source_files: list[Path],
) -> EdgeMap:
    """Build edges via textDocument/definition instead of references.

    JDTLS serializes references requests (~1-10s each), making the default
    references-based approach impractical for large projects. Definition
    queries are ~20ms each, so we scan source for call sites and resolve
    them via definition, then query implementations for polymorphic dispatch.
    """
    st = ctx.symbol_table

    pos_to_sym, line_to_syms = _build_definition_lookups(st)

    resolution = _resolve_definitions(adapter, ctx, source_files, pos_to_sym, line_to_syms)

    total_impl_resolved = _resolve_implementations(
        ctx, resolution.edge_set, resolution.impl_queries_pending, pos_to_sym, line_to_syms
    )

    total_iterated = 0
    if adapter.resolves_iterated_types:
        total_iterated = _resolve_iterated_types(ctx, resolution.edge_set, source_files, pos_to_sym, line_to_syms)

    logger.info(
        "Phase 2 summary: %d call sites, %d def resolved, %d impl resolved, %d iterated, %d raw edges",
        resolution.total_sites,
        resolution.total_resolved,
        total_impl_resolved,
        total_iterated,
        len(resolution.edge_set),
    )
    return resolution.edge_set


def _resolve_iterated_types(
    ctx: EdgeBuildContext,
    edge_set: EdgeMap,
    source_files: list[Path],
    pos_to_sym: dict[tuple[str, int, int], SymbolInfo],
    line_to_syms: dict[tuple[str, int], list[SymbolInfo]],
) -> int:
    """Edge from a loop to the type it enumerates.

    ``foreach (var x in bag)`` calls ``GetEnumerator`` on whatever ``bag`` is,
    but the syntax names only the value. A type query is the one request that
    names the type, so this is the only route to the edge.
    """
    st = ctx.symbol_table
    batch_size = 50
    resolved = 0

    for file_path in source_files:
        sites = ctx.source_inspector.find_iterated_expression_sites(file_path)
        if not sites:
            continue
        for start in range(0, len(sites), batch_size):
            batch = sites[start : start + batch_size]
            queries = [(file_path, site.lsp_line, site.lsp_column) for site in batch]
            try:
                results, _ = ctx.lsp.send_type_definition_batch(queries)
            except Exception as e:
                capture_error(
                    "static_analysis.type_definition_batch", e, extra={"file": file_path.name, "sites": len(batch)}
                )
                raise EdgeResolutionError(
                    f"Type-definition batch failed for {file_path.name} ({len(batch)} foreach sites): {e}"
                ) from e

            for index, site in enumerate(batch):
                caller = st.find_containing_symbol(file_path, site.lsp_line, site.lsp_column)
                if caller:
                    caller = st.lift_to_callable(caller)
                if not caller:
                    continue
                for result in results[index] if index < len(results) else []:
                    target = _resolve_definition_to_symbol(result, pos_to_sym, line_to_syms)
                    if target is None or not _is_valid_edge(caller, target):
                        continue
                    resolved += 1
                    _add_edge_call_site(edge_set, caller.qualified_name, target.qualified_name, site)
                    # The loop calls the enumerator, so name it too when the
                    # type declares one rather than inheriting it.
                    for enumerator in _members_named(target, st, "GetEnumerator"):
                        if _is_valid_edge(caller, enumerator):
                            _add_edge_call_site(edge_set, caller.qualified_name, enumerator.qualified_name, site)
    return resolved


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
) -> DefinitionResolution:
    """Phase 2a: Resolve call sites via textDocument/definition."""
    edge_set: EdgeMap = {}
    st = ctx.symbol_table
    total_files = len(source_files)
    total_sites = 0
    total_resolved = 0
    batch_size = 50
    impl_queries_pending: list[ImplementationQuery] = []

    dispatch = _build_dispatch_index(adapter, ctx, source_files) if adapter.expands_virtual_dispatch else None

    pbar = ProgressLogger("Phase 2 (definitions)", total_files, unit="file")
    # A failed batch loses its edges; count them so the summary can say so.
    unresolved_sites = 0
    unresolved_files: set[str] = set()
    for file_path in source_files:
        call_sites = ctx.source_inspector.find_call_sites(file_path)
        method_group_positions: set[tuple[int, int]] = set()
        if adapter.resolves_method_groups:
            known = {(site.lsp_line, site.lsp_column) for site in call_sites}
            for site in ctx.source_inspector.find_method_group_sites(file_path):
                if (site.lsp_line, site.lsp_column) in known:
                    continue
                method_group_positions.add((site.lsp_line, site.lsp_column))
                call_sites.append(site)
        collection_positions: set[tuple[int, int]] = set()
        if adapter.resolves_collection_initializers:
            collection_positions = {
                (site.lsp_line, site.lsp_column)
                for site in ctx.source_inspector.find_collection_initializer_sites(file_path)
            }
        if not call_sites:
            pbar.update(1)
            continue

        total_sites += len(call_sites)

        for batch_start in range(0, len(call_sites), batch_size):
            batch = call_sites[batch_start : batch_start + batch_size]
            queries = [(file_path, site.lsp_line, site.lsp_column) for site in batch]

            try:
                results, _ = ctx.lsp.send_definition_batch(queries)
            except Exception as e:
                capture_error(
                    "static_analysis.definition_batch", e, extra={"file": file_path.name, "sites": len(batch)}
                )
                raise EdgeResolutionError(
                    f"Definition batch failed for {file_path.name} ({len(batch)} call sites): {e}"
                ) from e

            for i, call_site in enumerate(batch):
                defs = results[i] if i < len(results) else []
                if not defs:
                    continue

                caller = st.find_containing_symbol(file_path, call_site.lsp_line, call_site.lsp_column)
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

                    # An argument position is a method group only if it resolves
                    # to something callable; otherwise it is an ordinary value.
                    if (call_site.lsp_line, call_site.lsp_column) in method_group_positions and not (
                        adapter.is_callable(target.kind) or adapter.is_class_like(target.kind)
                    ):
                        continue

                    if not _is_valid_edge(caller, target):
                        continue

                    _add_edge_call_site(edge_set, caller.qualified_name, target.qualified_name, call_site)

                    for override in _override_targets(target, st, dispatch):
                        if _is_valid_edge(caller, override):
                            _add_edge_call_site(edge_set, caller.qualified_name, override.qualified_name, call_site)

                    if (call_site.lsp_line, call_site.lsp_column) in collection_positions:
                        for adder in _members_named(target, st, "Add"):
                            if _is_valid_edge(caller, adder):
                                _add_edge_call_site(edge_set, caller.qualified_name, adder.qualified_name, call_site)

                    # If target is a callable with a class-like parent, also add edge to the parent class
                    if adapter.is_callable(target.kind) and target.parent_chain:
                        _, parent_kind = target.parent_chain[-1]
                        if adapter.is_class_like(parent_kind):
                            parent_qname = parent_qualified_name(target.qualified_name)
                            parent_sym = st.symbols.get(parent_qname)
                            if parent_sym is not None and _is_valid_edge(caller, parent_sym):
                                _add_edge_call_site(edge_set, caller.qualified_name, parent_qname, call_site)

                    # Queue implementation query for polymorphic dispatch
                    if adapter.is_callable(target.kind):
                        impl_queries_pending.append(
                            ImplementationQuery(
                                caller_qname=caller.qualified_name,
                                target_file=target.file_path,
                                target_line=target.start_line,
                                target_char=target.start_char,
                                call_site=call_site,
                            )
                        )

        pbar.set_postfix(edges=len(edge_set), resolved=total_resolved)
        pbar.update(1)
    pbar.finish()

    if unresolved_sites:
        logger.warning(
            "Phase 2: %d call site(s) across %d file(s) went unresolved after language-server failures; "
            "those edges are missing from this run",
            unresolved_sites,
            len(unresolved_files),
        )
    return DefinitionResolution(
        edge_set=edge_set,
        impl_queries_pending=impl_queries_pending,
        total_sites=total_sites,
        total_resolved=total_resolved,
    )


def _resolve_implementations(
    ctx: EdgeBuildContext,
    edge_set: EdgeMap,
    impl_queries_pending: list[ImplementationQuery],
    pos_to_sym: dict[tuple[str, int, int], SymbolInfo],
    line_to_syms: dict[tuple[str, int], list[SymbolInfo]],
) -> int:
    """Phase 2b: Resolve implementations for polymorphic call targets.

    Adds implementation edges to edge_set in-place. Returns total_impl_resolved.
    """
    st = ctx.symbol_table
    batch_size = 50

    target_pos_to_callers: dict[tuple[str, int, int], list[tuple[str, CallSite]]] = {}
    for query in impl_queries_pending:
        tgt_key = (str(query.target_file), query.target_line, query.target_char)
        target_pos_to_callers.setdefault(tgt_key, []).append((query.caller_qname, query.call_site))

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
            # Costs the polymorphic edges for this batch: calls resolve to the
            # declaration and never reach the implementations that run.
            logger.warning("Implementation batch failed (%d targets): %s", len(batch_keys), e)
            record_degradation("implementation_batch", f"{type(e).__name__}: {e}", items=len(batch_keys))
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

                for caller_qname, call_site in callers:
                    caller_sym = st.symbols.get(caller_qname)
                    if caller_sym and _is_valid_edge(caller_sym, impl_sym):
                        _add_edge_call_site(edge_set, caller_qname, impl_sym.qualified_name, call_site)

        pbar.set_postfix(edges=len(edge_set), resolved=total_impl_resolved)
        pbar.update(len(batch_keys))
    pbar.finish()

    return total_impl_resolved


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _call_site(file_path: Path, line: int, column: int) -> CallSite:
    """Convert LSP's zero-based position to the public one-based call-site shape."""
    return CallSite.from_lsp_position(file=str(file_path), line=line, column=column)


def _add_edge_site(edge_set: EdgeMap, source: str, destination: str, file_path: Path, line: int, column: int) -> None:
    _add_edge_call_site(edge_set, source, destination, _call_site(file_path, line, column))


def _add_edge_call_site(edge_set: EdgeMap, source: str, destination: str, call_site: CallSite) -> None:
    sites = edge_set.setdefault((source, destination), [])
    if call_site not in sites:
        sites.append(call_site)


def _build_dispatch_index(
    adapter: EdgeBuildAdapter,
    ctx: EdgeBuildContext,
    source_files: list[Path],
) -> DispatchIndex:
    """Inheritance and member modifiers, read from source, for virtual-dispatch expansion."""
    st = ctx.symbol_table
    classes_by_file: dict[str, dict[str, SymbolInfo]] = {}
    declaring_files: dict[str, set[tuple[str, int]]] = {}
    for sym in st.symbols.values():
        if not adapter.is_class_like(sym.kind):
            continue
        classes_by_file.setdefault(str(sym.file_path), {}).setdefault(sym.name, sym)
        declaring_files.setdefault(sym.name, set()).add((str(sym.file_path), sym.start_line))

    # A base is only ever its simple name in source, so a name two namespaces
    # both declare cannot be told apart — and expanding it would wire every
    # subclass of one to calls on the other.
    ambiguous = {name for name, sites in declaring_files.items() if len(sites) > 1}

    subclasses: dict[str, list[SymbolInfo]] = {}
    modifiers: dict[tuple[str, str], frozenset[str]] = {}
    for file_path in source_files:
        declared = classes_by_file.get(str(file_path))
        if not declared:
            continue
        modifiers.update(ctx.source_inspector.find_member_modifiers(file_path))
        for type_name, bases in ctx.source_inspector.find_type_bases(file_path):
            sub = declared.get(type_name)
            if sub is None:
                continue
            for base in bases:
                if base not in ambiguous:
                    subclasses.setdefault(base, []).append(sub)
    if ambiguous:
        logger.info("Skipped %d ambiguous base type name(s) for virtual dispatch", len(ambiguous))
    return DispatchIndex(subclasses=subclasses, modifiers=modifiers, ambiguous=ambiguous)


def _members_named(target: SymbolInfo, st: SymbolTable, name: str) -> list[SymbolInfo]:
    """Overloads of *name* declared on *target*."""
    found: list[SymbolInfo] = []
    for owner in _member_owner_prefixes(target):
        prefix = f"{owner}.{name}"
        for qualified_name, symbol in st.symbols.items():
            if not qualified_name.startswith(prefix):
                continue
            # Only that member, not a longer name starting with it.
            if qualified_name[len(prefix) :].startswith(("(", "<")):
                found.append(symbol)
        if found:
            break
    return found


def _member_owner_prefixes(target: SymbolInfo) -> list[str]:
    """Qualified-name prefixes a member of *target* may be filed under.

    A class whose name matches its file collapses into the file's segment, so
    ``InlineValidator<T>`` in ``InlineValidator.cs`` owns ``...InlineValidator.Add``
    rather than ``...InlineValidator.InlineValidator<T>.Add``.
    """
    prefixes = [target.qualified_name]
    parent = parent_qualified_name(target.qualified_name)
    simple = target.qualified_name[len(parent) + 1 :].split("<", 1)[0] if parent else ""
    if parent and simple and parent.split(".")[-1] == simple:
        prefixes.append(parent)
    return prefixes


def _override_targets(
    target: SymbolInfo,
    st: SymbolTable,
    dispatch: DispatchIndex | None,
) -> list[SymbolInfo]:
    """Same-named members on every type deriving from the target's own type.

    A call through a base-typed reference resolves to the base declaration,
    which for an abstract member has no body; the overrides are what actually run.
    """
    if dispatch is None:
        return []
    owner_qname = parent_qualified_name(target.qualified_name)
    owner = st.symbols.get(owner_qname)
    if owner is None or owner.name in dispatch.ambiguous or not dispatch.subclasses.get(owner.name):
        return []

    member = target.qualified_name[len(owner_qname) + 1 :]
    overrides: list[SymbolInfo] = []
    seen: set[str] = set()
    stack = list(dispatch.subclasses[owner.name])
    while stack:
        sub = stack.pop()
        if sub.qualified_name in seen:
            continue
        seen.add(sub.qualified_name)
        override = st.symbols.get(f"{sub.qualified_name}.{member}")
        if override is not None and dispatch.dispatches_to(owner, sub.name, member):
            overrides.append(override)
        stack.extend(dispatch.subclasses.get(sub.name, []))
    return overrides


def _is_valid_edge(caller: SymbolInfo, target: SymbolInfo) -> bool:
    """Check if an edge between caller and target is valid."""
    if is_self_or_container_edge(caller.qualified_name, target.qualified_name):
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
    location = definition_location(def_result)
    if location is None:
        return None
    file_path, line, char = location
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
