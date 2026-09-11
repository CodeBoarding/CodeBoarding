"""Edge building strategies for call-graph construction.

- build_edges_via_definitions: tree-sitter finds the call sites and the server answers
  ``textDocument/definition`` at each. Used by Java, C#, Python, TypeScript and JavaScript.
- build_edges_via_references: every declaration asks ``textDocument/references``. Still the
  default for the adapters not yet moved across.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from static_analyzer.engine.edge_build_context import EdgeBuildContext
from static_analyzer.engine.progress import ProgressLogger
from static_analyzer.config import NodeType
from static_analyzer.engine.lsp_constants import (
    CALLABLE_KINDS,
    CLASS_LIKE_KINDS,
)
from static_analyzer.engine.models import CallSite, ExternalCallSite, SymbolInfo
from static_analyzer.engine.protocols import EdgeBuildAdapter
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable
from static_analyzer.engine.utils import definition_location, uri_to_path
from static_analyzer.graph_definitions import CALL, COLLECTION_INITIALIZER, ITERATED, MatchCounts, METHOD_GROUP
from static_analyzer.internal_references import is_self_or_container_edge, parent_qualified_name, simple_name

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


class CallEdgeSink:
    """Every edge one resolved call site contributes, so each route to a target adds the same set.

    The target itself, the overrides a base-typed call dispatches to, the ``Add`` a collection
    initializer runs, the class a method belongs to, and the implementation query that finishes
    a polymorphic call.
    """

    def __init__(self, adapter: EdgeBuildAdapter, st: SymbolTable, dispatch: DispatchIndex | None) -> None:
        self.edges: EdgeMap = {}
        self.impl_queries: list[ImplementationQuery] = []
        self._adapter = adapter
        self._st = st
        self._dispatch = dispatch

    def add(self, caller: SymbolInfo, target: SymbolInfo, call_site: CallSite, collection: bool = False) -> None:
        """Record the call from *caller* to *target*, unless the pair is not an edge at all."""
        if not _is_valid_edge(caller, target):
            return
        # Guards keep the innermost caller; only the credit line rolls up.
        attributed = self._st.attribution_symbol(caller)
        if not _is_valid_edge(attributed, target):
            return

        _add_edge_call_site(self.edges, attributed.qualified_name, target.qualified_name, call_site)

        extra = list(_override_targets(target, self._st, self._dispatch))
        if collection:
            extra.extend(_members_named(target, self._st, "Add"))
        if self._adapter.is_callable(target.kind) and target.parent_chain:
            _, parent_kind = target.parent_chain[-1]
            if self._adapter.is_class_like(parent_kind):
                parent = self._st.symbols.get(parent_qualified_name(target.qualified_name))
                if parent is not None:
                    extra.append(parent)
        for other in extra:
            if _is_valid_edge(caller, other) and _is_valid_edge(attributed, other):
                _add_edge_call_site(self.edges, attributed.qualified_name, other.qualified_name, call_site)

        if self._adapter.is_callable(target.kind):
            self.impl_queries.append(
                ImplementationQuery(
                    caller_qname=attributed.qualified_name,
                    target_file=target.file_path,
                    target_line=target.start_line,
                    target_char=target.start_char,
                    call_site=call_site,
                )
            )


class SymbolIndex:
    """The symbol table by position, by line and by name, for resolving definition results."""

    def __init__(self, st: SymbolTable, inspector: SourceInspector) -> None:
        self.counts = MatchCounts()
        self._inspector = inspector
        self._by_position: dict[tuple[str, int, int], SymbolInfo] = {}
        self._by_line: dict[tuple[str, int], list[SymbolInfo]] = {}
        self._by_name: dict[tuple[str, str], dict[tuple[str, int, int], SymbolInfo]] = {}
        for sym in st.symbols.values():
            position = sym.definition_location
            self._keep_most_specific(self._by_position, position, sym)
            self._by_line.setdefault((str(sym.file_path), sym.start_line), []).append(sym)
            if sym.kind in CALLABLE_KINDS or sym.kind in CLASS_LIKE_KINDS:
                at = self._by_name.setdefault((str(sym.file_path), simple_name(sym.qualified_name)), {})
                self._keep_most_specific(at, position, sym)

    def resolve(self, def_result: dict) -> SymbolInfo | None:
        """The symbol a definition result names, or None when nothing names it unambiguously.

        Exact position, else the innermost symbol declared on that line that still contains
        the position, else the sole callable or class the file declares under the name written
        there -- the overload set a server answers with a signature line for.
        """
        location = definition_location(def_result)
        if location is None:
            self.counts.rejected += 1
            return None
        file_path, line, char = location
        file_key = str(file_path)

        exact = self._by_position.get((file_key, line, char))
        if exact is not None:
            self.counts.exact += 1
            return exact

        containing = max(
            (sym for sym in self._by_line.get((file_key, line), []) if sym.start_char <= char and sym.end_line >= line),
            key=lambda sym: (sym.start_char, -sym.end_line, len(sym.qualified_name)),
            default=None,
        )
        if containing is not None:
            self.counts.same_line += 1
            return containing

        name = self._inspector.identifier_at(file_path, line, char)
        declared = self._by_name.get((file_key, name)) if name else None
        if declared is not None and len(declared) == 1:
            self.counts.name_at_definition += 1
            return next(iter(declared.values()))

        self.counts.rejected += 1
        return None

    @staticmethod
    def _keep_most_specific(
        holder: dict[tuple[str, int, int], SymbolInfo], position: tuple[str, int, int], sym: SymbolInfo
    ) -> None:
        """Dual registrations share a position; the longest qualified name is the specific one."""
        held = holder.get(position)
        if held is None or len(sym.qualified_name) > len(held.qualified_name):
            holder[position] = sym


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
            # Every guard above sees the innermost container, which is what decides whether
            # this reference is an edge at all. Only the credit line is rolled up.
            attributed = st.attribution_symbol(container).qualified_name
            if attributed == sym.qualified_name or sym.qualified_name.startswith(attributed + "."):
                continue
            _add_edge_site(edge_set, attributed, sym.qualified_name, ref_file, ref_line, ref_char)

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
    index = SymbolIndex(ctx.symbol_table, ctx.source_inspector)

    resolution = _resolve_definitions(adapter, ctx, source_files, index)

    total_impl_resolved = _resolve_implementations(ctx, resolution.edge_set, resolution.impl_queries_pending, index)

    total_iterated = 0
    if adapter.resolves_iterated_types:
        total_iterated = _resolve_iterated_types(ctx, resolution.edge_set, source_files, index)

    logger.info(
        "Phase 2 summary: %d call sites, %d def resolved, %d impl resolved, %d iterated, %d raw edges",
        resolution.total_sites,
        resolution.total_resolved,
        total_impl_resolved,
        total_iterated,
        len(resolution.edge_set),
    )
    logger.info("Phase 2 definition matches: %s", index.counts.summary())
    return resolution.edge_set


def _resolve_iterated_types(
    ctx: EdgeBuildContext,
    edge_set: EdgeMap,
    source_files: list[Path],
    index: SymbolIndex,
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
                logger.warning(
                    "Type-definition batch failed for %s (%d foreach sites): %s", file_path.name, len(batch), e
                )
                continue

            for offset, site in enumerate(batch):
                caller = st.find_containing_symbol(file_path, site.lsp_line, site.lsp_column)
                if caller:
                    caller = st.lift_to_callable(caller)
                if not caller:
                    continue
                for result in results[offset] if offset < len(results) else []:
                    target = index.resolve(result)
                    if target is None:
                        _record_external_call_site(ctx, st.attribution_symbol(caller), result, site, ITERATED)
                        continue
                    if not _is_valid_edge(caller, target):
                        continue
                    resolved += 1
                    attributed = st.attribution_symbol(caller)
                    if not _is_valid_edge(attributed, target):
                        continue
                    _add_edge_call_site(edge_set, attributed.qualified_name, target.qualified_name, site)
                    # The loop calls the enumerator, so name it too when the
                    # type declares one rather than inheriting it.
                    for enumerator in _members_named(target, st, "GetEnumerator"):
                        if _is_valid_edge(caller, enumerator) and _is_valid_edge(attributed, enumerator):
                            _add_edge_call_site(edge_set, attributed.qualified_name, enumerator.qualified_name, site)
    return resolved


def _resolve_definitions(
    adapter: EdgeBuildAdapter,
    ctx: EdgeBuildContext,
    source_files: list[Path],
    index: SymbolIndex,
) -> DefinitionResolution:
    """Phase 2a: Resolve call sites via textDocument/definition."""
    st = ctx.symbol_table
    si = ctx.source_inspector
    total_files = len(source_files)
    total_sites = 0
    total_resolved = 0
    batch_size = 50

    dispatch = _build_dispatch_index(adapter, ctx, source_files) if adapter.expands_virtual_dispatch else None
    sink = CallEdgeSink(adapter, st, dispatch)

    pbar = ProgressLogger("Phase 2 (definitions)", total_files, unit="file")
    for file_path in source_files:
        call_sites = si.find_call_sites(file_path)
        method_group_positions: set[tuple[int, int]] = set()
        if adapter.resolves_method_groups:
            known = {(site.lsp_line, site.lsp_column) for site in call_sites}
            for site in si.find_method_group_sites(file_path):
                if (site.lsp_line, site.lsp_column) in known:
                    continue
                method_group_positions.add((site.lsp_line, site.lsp_column))
                call_sites.append(site)
        collection_positions: set[tuple[int, int]] = set()
        if adapter.resolves_collection_initializers:
            collection_positions = {
                (site.lsp_line, site.lsp_column) for site in si.find_collection_initializer_sites(file_path)
            }
        if not call_sites:
            pbar.update(1)
            continue

        total_sites += len(call_sites)
        unresolved: list[CallSite] = []

        for batch_start in range(0, len(call_sites), batch_size):
            batch = call_sites[batch_start : batch_start + batch_size]
            queries = [(file_path, site.lsp_line, site.lsp_column) for site in batch]

            try:
                results, _ = ctx.lsp.send_definition_batch(queries)
            except Exception as e:
                logger.warning("Definition batch failed for %s: %s", file_path.name, e)
                continue

            for i, call_site in enumerate(batch):
                defs = results[i] if i < len(results) else []
                position = (call_site.lsp_line, call_site.lsp_column)
                kind = CALL
                if position in method_group_positions:
                    kind = METHOD_GROUP
                elif position in collection_positions:
                    kind = COLLECTION_INITIALIZER

                if not defs:
                    if kind == CALL:
                        unresolved.append(call_site)
                    continue

                caller = st.find_containing_symbol(file_path, call_site.lsp_line, call_site.lsp_column)
                if caller:
                    caller = st.lift_to_callable(caller)
                if not caller:
                    continue

                resolved_here = False
                for def_result in defs:
                    target = index.resolve(def_result)
                    if not target:
                        _record_external_call_site(ctx, st.attribution_symbol(caller), def_result, call_site, kind)
                        continue
                    total_resolved += 1
                    resolved_here = True

                    # An argument position is a method group only if it resolves to something
                    # callable; otherwise it is an ordinary value.
                    if kind == METHOD_GROUP and not (
                        adapter.is_callable(target.kind)
                        or adapter.is_class_like(target.kind)
                        or si.declares_function_value(target.file_path, target.start_line, target.start_char)
                    ):
                        continue

                    sink.add(caller, target, call_site, collection=position in collection_positions)

                if not resolved_here and kind == CALL:
                    unresolved.append(call_site)

        total_resolved += _resolve_through_receivers(ctx, index, sink, file_path, unresolved)

        pbar.set_postfix(edges=len(sink.edges), resolved=total_resolved)
        pbar.update(1)
    pbar.finish()

    return DefinitionResolution(
        edge_set=sink.edges,
        impl_queries_pending=sink.impl_queries,
        total_sites=total_sites,
        total_resolved=total_resolved,
    )


def _resolve_through_receivers(
    ctx: EdgeBuildContext,
    index: SymbolIndex,
    sink: CallEdgeSink,
    file_path: Path,
    unresolved: list[CallSite],
) -> int:
    """Edges for ``receiver.member(...)`` whose member left the repository but whose receiver did not.

    Why: a repository object typed as something external -- mermaid's ``log`` typed as
    ``console`` -- sends every definition query on the member into a type declaration, while
    the receiver still names the object that holds the member being run.
    """
    if not unresolved:
        return 0
    st = ctx.symbol_table
    receivers = ctx.source_inspector.receiver_member_calls(file_path)
    # One query per receiver, not per call: `log.warn` and `log.info` name the same object.
    by_receiver: dict[tuple[int, int], list[tuple[CallSite, str]]] = {}
    for site in unresolved:
        found = receivers.get((site.lsp_line, site.lsp_column))
        if found is not None:
            by_receiver.setdefault((found.line, found.column), []).append((site, found.member))
    if not by_receiver:
        return 0

    positions = list(by_receiver)
    resolved = 0
    for batch_start in range(0, len(positions), 50):
        batch = positions[batch_start : batch_start + 50]
        try:
            results, _ = ctx.lsp.send_definition_batch([(file_path, line, column) for line, column in batch])
        except Exception as e:
            logger.warning("Receiver definition batch failed for %s: %s", file_path.name, e)
            continue
        for i, position in enumerate(batch):
            for def_result in results[i] if i < len(results) else []:
                receiver = index.resolve(def_result)
                if receiver is None:
                    continue
                for call_site, member in by_receiver[position]:
                    target = st.symbols.get(f"{receiver.qualified_name}.{member}")
                    if target is None:
                        continue
                    caller = st.find_containing_symbol(file_path, call_site.lsp_line, call_site.lsp_column)
                    if caller:
                        caller = st.lift_to_callable(caller)
                    if not caller:
                        continue
                    resolved += 1
                    sink.add(caller, target, call_site)
    return resolved


def _resolve_implementations(
    ctx: EdgeBuildContext,
    edge_set: EdgeMap,
    impl_queries_pending: list[ImplementationQuery],
    index: SymbolIndex,
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
            logger.warning("Implementation batch failed: %s", e)
            pbar.update(len(batch_keys))
            continue

        for j, tgt_key in enumerate(batch_keys):
            impls = impl_results[j] if j < len(impl_results) else []
            callers = target_pos_to_callers[tgt_key]

            for impl_result in impls:
                impl_sym = index.resolve(impl_result)
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


def _record_external_call_site(
    ctx: EdgeBuildContext, caller: SymbolInfo, def_result: dict, call_site: CallSite, kind: str
) -> None:
    """Keep a definition that landed in a file this engine never named, for the merged graph."""
    location = definition_location(def_result)
    if location is None or str(location[0]) in ctx.symbol_table.file_symbols:
        return
    ctx.external_call_sites.append(
        ExternalCallSite(
            caller=caller.qualified_name,
            file=str(location[0]),
            line=location[1],
            character=location[2],
            call_site=call_site,
            kind=kind,
        )
    )


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
