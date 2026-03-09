"""Core algorithm for building call flow graphs using LSP."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from static_analyzer.engine.hierarchy_builder import HierarchyBuilder
from static_analyzer.engine.language_adapter import (
    SYMBOL_KIND_CONSTANT,
    SYMBOL_KIND_VARIABLE,
    LanguageAdapter,
)
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.models import CallFlowGraph, LanguageAnalysisResult, SymbolInfo
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable
from static_analyzer.engine.utils import uri_to_path

logger = logging.getLogger(__name__)

# Batch size for did_open to avoid overwhelming LSP servers
DID_OPEN_BATCH_SIZE = 50


class CallGraphBuilder:
    """Builds a call flow graph using LSP document symbols and references."""

    def __init__(
        self,
        lsp_client: LSPClient,
        adapter: LanguageAdapter,
        project_root: Path,
    ) -> None:
        self._lsp = lsp_client
        self._adapter = adapter
        self._root = project_root.resolve()

        self._symbol_table = SymbolTable(adapter)
        self._source_inspector = SourceInspector()

    @property
    def symbol_table(self) -> SymbolTable:
        """Public access to the symbol table for result conversion."""
        return self._symbol_table

    def build(self, source_files: list[Path]) -> LanguageAnalysisResult:
        """Run the full analysis pipeline and return results."""
        t_pipeline = time.monotonic()

        self._discover_symbols(source_files)
        t_symbols_done = time.monotonic()
        logger.info("Phase 1 total (discover symbols): %.1fs", t_symbols_done - t_pipeline)

        self._symbol_table.build_indices()
        t_indices_done = time.monotonic()
        logger.info("Build indices: %.1fs", t_indices_done - t_symbols_done)

        if self._adapter.use_definition_based_edges:
            edge_set = self._build_edges_via_definitions(source_files)
        else:
            edge_set = self._build_edges()
        t_edges_done = time.monotonic()
        logger.info("Phase 2 total (build edges): %.1fs", t_edges_done - t_indices_done)

        hierarchy_builder = HierarchyBuilder(self._lsp, self._symbol_table, self._source_inspector, self._adapter)
        hierarchy = hierarchy_builder.build()
        t_hierarchy_done = time.monotonic()
        logger.info("Phase 3 (hierarchy): %.1fs", t_hierarchy_done - t_edges_done)

        package_deps = self._build_package_deps(edge_set, source_files)
        t_pkgdeps_done = time.monotonic()
        logger.info("Phase 4 (package deps): %.1fs", t_pkgdeps_done - t_hierarchy_done)

        # Build references from primary symbols only (not unqualified aliases).
        # _primary_file_symbols excludes dual-registration aliases, so each
        # physical symbol appears exactly once with its class-qualified name.
        references: dict[str, dict] = {}
        primary_qnames: set[str] = set()
        for syms in self._symbol_table.primary_file_symbols.values():
            for sym in syms:
                primary_qnames.add(sym.qualified_name)
        for qname in sorted(primary_qnames):
            sym = self._symbol_table.symbols[qname]
            if self._adapter.is_reference_worthy(sym.kind):
                ref_key = self._adapter.build_reference_key(qname)
                references[ref_key] = {
                    "name": sym.name,
                    "qualified_name": qname,
                    "kind": sym.kind,
                    "file": str(sym.file_path),
                    "line": sym.start_line,
                }

        cfg = CallFlowGraph.from_edge_set(edge_set)
        abs_files = sorted(str(f.resolve()) for f in source_files)

        logger.info(
            "Pipeline complete: %d files, %d symbols, %d edges, %d hierarchy entries in %.1fs",
            len(source_files),
            len(self._symbol_table.symbols),
            len(edge_set),
            len(hierarchy),
            time.monotonic() - t_pipeline,
        )

        return LanguageAnalysisResult(
            references=references,
            hierarchy=hierarchy,
            cfg=cfg,
            package_dependencies=package_deps,
            source_files=abs_files,
        )

    def _discover_symbols(self, source_files: list[Path]) -> None:
        """Phase 0+1: Open files, wait for indexing, then extract all symbols."""
        total = len(source_files)
        t_open_start = time.monotonic()
        logger.info("Phase 0 (open): opening %d files for indexing", total)
        for i in range(0, total, DID_OPEN_BATCH_SIZE):
            batch = source_files[i : i + DID_OPEN_BATCH_SIZE]
            for file_path in batch:
                self._lsp.did_open(file_path, self._adapter.language_id)
            opened = min(i + DID_OPEN_BATCH_SIZE, total)
            logger.info("Phase 0 (open): %d/%d files opened", opened, total)
            if i + DID_OPEN_BATCH_SIZE < total:
                time.sleep(0.1)
        logger.info("did_open %d files: %.1fs", total, time.monotonic() - t_open_start)

        # Synchronization probe — cache result to avoid re-querying first file.
        # Use a long timeout (5 min) because LSP servers may need to index the
        # entire project before responding (e.g. gopls on large Go projects).
        probe_result: list[dict] | None = None
        logger.info("Phase 0 (open): waiting for LSP server indexing...")
        t_probe = time.monotonic()
        if source_files:
            probe_result = self._lsp.document_symbol(source_files[0], timeout=300)
        logger.info(
            "Sync probe: %.1fs (%d symbols)",
            time.monotonic() - t_probe,
            len(probe_result) if probe_result else 0,
        )

        total = len(source_files)
        for idx, file_path in enumerate(source_files, 1):
            if idx == 1 and probe_result is not None:
                symbols = probe_result
            else:
                symbols = self._lsp.document_symbol(file_path)
            self._symbol_table.register_symbols(file_path, symbols, parent_chain=[], project_root=self._root)
            if idx % 50 == 0 or idx == total:
                logger.info(
                    "Phase 1 (symbols): %d/%d files processed, %d symbols so far",
                    idx,
                    total,
                    len(self._symbol_table.symbols),
                )

        logger.info("Discovered %d symbols across %d files", len(self._symbol_table.symbols), len(source_files))

        # Warmup probe: trigger the LSP server's cross-reference index build
        # by sending a single references request with a long timeout.
        # JDTLS builds its index lazily on the first references request,
        # which can take 10-60s on CI. Without this, the first batch of
        # references requests would all timeout waiting for the index.
        if source_files and self._adapter.references_per_query_timeout > 0:
            logger.info("Phase 1.5 (warmup): triggering LSP index build with a single references request...")
            t_warmup = time.monotonic()
            try:
                self._lsp.references(source_files[0], 0, 0)
            except (TimeoutError, Exception) as e:
                logger.warning("Warmup probe failed (non-fatal): %s", e)
            logger.info("Phase 1.5 (warmup): completed in %.1fs", time.monotonic() - t_warmup)

    def _build_edges(self) -> set[tuple[str, str]]:
        """Phase 2: For each trackable symbol, find references and build edges.

        Only includes references that are actual call sites (symbol followed by '(').
        This filters out type annotations, imports, assignments, and other non-call uses.
        """
        edge_set: set[tuple[str, str]] = set()
        st = self._symbol_table
        si = self._source_inspector

        trackable = sorted(
            [
                sym
                for sym in st.symbols.values()
                if self._adapter.should_track_for_edges(sym.kind) and not st.is_local_variable(sym)
            ],
            key=lambda s: s.qualified_name,
        )

        # Deduplicate by position
        pos_to_syms: dict[tuple[str, int, int], list[SymbolInfo]] = {}
        for sym in trackable:
            pos_key = sym.definition_location
            if pos_key not in pos_to_syms:
                pos_to_syms[pos_key] = []
            pos_to_syms[pos_key].append(sym)

        unique_positions = sorted(pos_to_syms.keys())
        total_unique = len(unique_positions)
        total_trackable = len(trackable)
        logger.info(
            "Phase 2 (edges): %d trackable symbols at %d unique positions (%.0f%% dedup)",
            total_trackable,
            total_unique,
            (1 - total_unique / max(total_trackable, 1)) * 100,
        )

        # Group positions by file for progress tracking
        file_positions: dict[str, list[tuple[str, int, int]]] = {}
        for pos_key in unique_positions:
            file_key = pos_key[0]
            if file_key not in file_positions:
                file_positions[file_key] = []
            file_positions[file_key].append(pos_key)

        total_files = len(file_positions)
        batch_size = self._adapter.references_batch_size
        per_query_timeout = self._adapter.references_per_query_timeout
        phase2_start = time.monotonic()
        positions_done = 0
        files_done: set[str] = set()
        refs_total = 0
        refs_call_sites = 0

        for batch_start in range(0, total_unique, batch_size):
            batch_positions = unique_positions[batch_start : batch_start + batch_size]

            queries = []
            for pos_key in batch_positions:
                representative = pos_to_syms[pos_key][0]
                queries.append((representative.file_path, representative.start_line, representative.start_char))

            try:
                result_list = self._lsp.send_references_batch(queries, per_query_timeout=per_query_timeout)
            except Exception as e:
                logger.warning("Batch references failed: %s", e)
                result_list = [[] for _ in queries]

            for i, pos_key in enumerate(batch_positions):
                syms_at_pos = pos_to_syms[pos_key]
                refs = result_list[i] if i < len(result_list) else []

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

                        # Filter references based on symbol kind to keep only
                        # meaningful call-graph edges:
                        #
                        # - Class-like symbols: only invocations (constructor calls).
                        #   Type annotations, isinstance, imports are filtered out.
                        #   Inheritance edges come from hierarchy data instead.
                        #
                        # - Variables/constants: only callable usage (invocations,
                        #   callback arguments, return values). Filters out reads,
                        #   assignments, and type references. Arrow functions and
                        #   closures stored in variables are correctly included.
                        #
                        # - Callable symbols (functions, methods, constructors):
                        #   all references kept — they represent meaningful
                        #   dependencies even as callbacks or method references.
                        if self._adapter.is_class_like(sym.kind):
                            if not si.is_invocation(ref_file, ref_line, ref_end_char):
                                continue
                        elif sym.kind == SYMBOL_KIND_CONSTANT:
                            # Constants are rarely callable — only track direct invocations
                            if not si.is_invocation(ref_file, ref_line, ref_end_char):
                                continue
                        elif sym.kind == SYMBOL_KIND_VARIABLE:
                            if not si.is_callable_usage(ref_file, ref_line, ref_char, ref_end_char):
                                continue

                        refs_call_sites += 1

                        container = st.find_containing_symbol(ref_file, ref_line, ref_char)
                        if not container:
                            continue
                        container = st.lift_to_callable(container)
                        if not container or container.qualified_name == sym.qualified_name:
                            continue
                        # Skip override/implementation declarations: if the reference
                        # location matches the container's own definition, this is a
                        # method override (e.g. Task.getType overriding Entity.getType),
                        # not an actual call site.
                        if (str(ref_file), ref_line) == (str(container.file_path), container.start_line):
                            continue
                        if sym.qualified_name.startswith(container.qualified_name + "."):
                            continue
                        edge_set.add((container.qualified_name, sym.qualified_name))

            positions_done += len(batch_positions)
            # Track completed files: a file is done when all its positions have been processed
            for pos_key in batch_positions:
                file_key = pos_key[0]
                file_pos_list = file_positions.get(file_key, [])
                if file_pos_list and pos_key == file_pos_list[-1]:
                    files_done.add(file_key)
            elapsed = time.monotonic() - phase2_start
            logger.info(
                "Phase 2 (edges): %d/%d positions queried, %d/%d files done, " "%d edges so far [%.0fs elapsed]",
                positions_done,
                total_unique,
                len(files_done),
                total_files,
                len(edge_set),
                elapsed,
            )

        logger.info(
            "Phase 2 (edges): %d/%d references were call sites (%.0f%% filtered out)",
            refs_call_sites,
            refs_total,
            (1 - refs_call_sites / max(refs_total, 1)) * 100,
        )

        edge_set = self._postprocess_edges(edge_set)
        logger.info("Found %d call graph edges", len(edge_set))
        return edge_set

    def _build_edges_via_definitions(self, source_files: list[Path]) -> set[tuple[str, str]]:
        """Phase 2 (definition-based): Build edges by resolving call sites via definition queries.

        Instead of querying references for every symbol (O(symbols) slow queries),
        scans each file for call-site identifiers and resolves them via
        textDocument/definition (O(call_sites) fast queries).

        For polymorphic calls (interface/abstract methods), also queries
        textDocument/implementation to find concrete implementations, creating
        edges to all known implementations (same as the references-based approach).

        This is dramatically faster for servers that serialize references requests
        (e.g. JDTLS: definition ~20ms vs references ~1-10s per query).
        """
        edge_set: set[tuple[str, str]] = set()
        st = self._symbol_table
        si = self._source_inspector

        # Build position-based lookups for resolving definition results.
        # For pos_to_sym, prefer the symbol with the longest qualified name
        # at each position (e.g. Container.Item.describe() over Container.describe()).
        pos_to_sym: dict[tuple[str, int, int], SymbolInfo] = {}
        line_to_syms: dict[tuple[str, int], list[SymbolInfo]] = {}
        for sym in st.symbols.values():
            pos = sym.definition_location
            existing = pos_to_sym.get(pos)
            if existing is None or len(sym.qualified_name) > len(existing.qualified_name):
                pos_to_sym[pos] = sym
            key = (str(sym.file_path), sym.start_line)
            line_to_syms.setdefault(key, []).append(sym)

        total_files = len(source_files)
        total_sites = 0
        total_resolved = 0
        total_impl_queries = 0
        total_impl_resolved = 0
        batch_size = 50
        phase2_start = time.monotonic()

        # Collect implementation queries to batch after definition resolution.
        # Each entry: (caller_qname, target_file, target_line, target_char)
        impl_queries_pending: list[tuple[str, Path, int, int]] = []

        for file_idx, file_path in enumerate(source_files, 1):
            call_sites = si.find_call_sites(file_path)
            if not call_sites:
                if file_idx % 50 == 0 or file_idx == total_files:
                    elapsed = time.monotonic() - phase2_start
                    logger.info(
                        "Phase 2 (definitions): %d/%d files, %d sites, %d resolved, %d edges [%.0fs]",
                        file_idx,
                        total_files,
                        total_sites,
                        total_resolved,
                        len(edge_set),
                        elapsed,
                    )
                continue

            total_sites += len(call_sites)

            for batch_start in range(0, len(call_sites), batch_size):
                batch = call_sites[batch_start : batch_start + batch_size]
                queries = [(file_path, line, col) for line, col in batch]

                try:
                    results = self._lsp.send_definition_batch(queries)
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
                        target = self._resolve_definition_to_symbol(def_result, pos_to_sym, line_to_syms)
                        if not target:
                            continue
                        total_resolved += 1

                        if not self._is_valid_edge(caller, target):
                            continue

                        edge_set.add((caller.qualified_name, target.qualified_name))

                        # If target is a constructor, also add edge to parent class
                        if self._adapter.is_callable(target.kind):
                            # Find parent class for constructors
                            if target.parent_chain:
                                _, parent_kind = target.parent_chain[-1]
                                if self._adapter.is_class_like(parent_kind):
                                    # Build parent class qualified name
                                    parent_qname = target.qualified_name.rsplit(".", 1)[0]
                                    # Strip constructor params to get class name
                                    paren_idx = parent_qname.find("(")
                                    if paren_idx != -1:
                                        parent_qname = parent_qname[:paren_idx]
                                    if parent_qname in st.symbols:
                                        parent_sym = st.symbols[parent_qname]
                                        if self._is_valid_edge(caller, parent_sym):
                                            edge_set.add((caller.qualified_name, parent_qname))

                            # Queue implementation query for polymorphic dispatch
                            impl_queries_pending.append(
                                (
                                    caller.qualified_name,
                                    target.file_path,
                                    target.start_line,
                                    target.start_char,
                                )
                            )

            if file_idx % 50 == 0 or file_idx == total_files:
                elapsed = time.monotonic() - phase2_start
                logger.info(
                    "Phase 2 (definitions): %d/%d files, %d sites, %d resolved, %d edges [%.0fs]",
                    file_idx,
                    total_files,
                    total_sites,
                    total_resolved,
                    len(edge_set),
                    elapsed,
                )

        # Phase 2b: resolve implementations for polymorphic call targets.
        # Deduplicate by target position first (many callers may call the same interface method).
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

        for batch_start in range(0, len(unique_impl_targets), batch_size):
            batch_keys = unique_impl_targets[batch_start : batch_start + batch_size]
            queries = [(Path(fk), ln, ch) for fk, ln, ch in batch_keys]

            try:
                impl_results = self._lsp.send_implementation_batch(queries)
            except Exception as e:
                logger.warning("Implementation batch failed: %s", e)
                continue

            for j, tgt_key in enumerate(batch_keys):
                impls = impl_results[j] if j < len(impl_results) else []
                callers = target_pos_to_callers[tgt_key]

                for impl_result in impls:
                    impl_sym = self._resolve_definition_to_symbol(impl_result, pos_to_sym, line_to_syms)
                    if not impl_sym:
                        continue
                    total_impl_resolved += 1

                    for caller_qname in callers:
                        caller_sym = st.symbols.get(caller_qname)
                        if caller_sym and self._is_valid_edge(caller_sym, impl_sym):
                            edge_set.add((caller_qname, impl_sym.qualified_name))

            if batch_start + batch_size >= len(unique_impl_targets) or (batch_start // batch_size) % 10 == 0:
                elapsed = time.monotonic() - phase2_start
                done = min(batch_start + batch_size, len(unique_impl_targets))
                logger.info(
                    "Phase 2b (implementations): %d/%d targets queried, %d impl resolved, %d edges [%.0fs]",
                    done,
                    total_impl_queries,
                    total_impl_resolved,
                    len(edge_set),
                    elapsed,
                )

        logger.info(
            "Phase 2 summary: %d call sites, %d def resolved, %d impl resolved, %d raw edges",
            total_sites,
            total_resolved,
            total_impl_resolved,
            len(edge_set),
        )

        edge_set = self._postprocess_edges(edge_set)
        logger.info("Found %d call graph edges (definition-based)", len(edge_set))
        return edge_set

    def _postprocess_edges(self, edge_set: set[tuple[str, str]]) -> set[tuple[str, str]]:
        """Deduplicate edges by definition location and expand constructor edges.

        Shared post-processing for both references-based and definition-based
        edge building strategies.
        """
        st = self._symbol_table

        # Deduplicate edges that refer to the same symbol pair via different
        # qualified names (aliases from dual registration). Keep the edge with
        # the longest combined qualified names (class-qualified over unqualified).
        # Also remove alias self-edges (same definition location for src and dst).
        pos_to_edge: dict[tuple, tuple[str, str]] = {}
        alias_self_edges = 0
        for src, dst in edge_set:
            src_sym = st.symbols.get(src)
            dst_sym = st.symbols.get(dst)
            if src_sym and dst_sym:
                if src_sym.definition_location == dst_sym.definition_location:
                    alias_self_edges += 1
                    continue
                edge_key = (src_sym.definition_location, dst_sym.definition_location)
                existing = pos_to_edge.get(edge_key)
                if existing is None or (len(src) + len(dst), src, dst) > (
                    len(existing[0]) + len(existing[1]),
                    existing[0],
                    existing[1],
                ):
                    pos_to_edge[edge_key] = (src, dst)
            else:
                edge_key = (src, dst)
                if edge_key not in pos_to_edge:
                    pos_to_edge[edge_key] = (src, dst)
        edge_set = set(pos_to_edge.values())
        if alias_self_edges:
            logger.info("Removed %d alias self-edges (same definition location)", alias_self_edges)

        # Constructor expansion: when a class has real constructors in the
        # symbol table, add edges to those constructors alongside the class edge.
        constructor_edges: set[tuple[str, str]] = set()
        for src, dst in edge_set:
            dst_sym = st.symbols.get(dst)
            if dst_sym and self._adapter.is_class_like(dst_sym.kind):
                ctors = st.class_to_ctors.get(dst)
                if ctors:
                    for ctor_name in ctors:
                        constructor_edges.add((src, ctor_name))
        edge_set |= constructor_edges

        return edge_set

    def _is_valid_edge(self, caller: SymbolInfo, target: SymbolInfo) -> bool:
        """Check if an edge between caller and target is valid."""
        if target.qualified_name == caller.qualified_name:
            return False
        if target.qualified_name.startswith(caller.qualified_name + "."):
            return False
        if caller.qualified_name.startswith(target.qualified_name + "."):
            return False
        if target.definition_location == caller.definition_location:
            return False
        # Skip override declarations (same file and line as caller definition)
        if (str(target.file_path), target.start_line) == (str(caller.file_path), caller.start_line):
            return False
        return True

    def _resolve_definition_to_symbol(
        self,
        def_result: dict,
        pos_to_sym: dict[tuple[str, int, int], SymbolInfo],
        line_to_syms: dict[tuple[str, int], list[SymbolInfo]],
    ) -> SymbolInfo | None:
        """Resolve a definition LSP result to a SymbolInfo in our table."""
        # Handle both Location and LocationLink formats
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

        # Fuzzy: match on (file, line) — pick the best candidate.
        # Prefer callable > class > other, then longest qualified name (most specific).
        # The length tiebreaker ensures inner-class symbols like Container.Item.describe()
        # win over their shorter aliases like Container.describe().
        candidates = line_to_syms.get((file_key, line), [])
        if candidates:
            best = self._best_candidate(candidates)
            if best:
                return best

        # Try adjacent lines (definition range start vs selectionRange start)
        for delta in (1, -1, 2, -2):
            candidates = line_to_syms.get((file_key, line + delta), [])
            if candidates:
                best = self._best_candidate(candidates)
                if best:
                    return best

        return None

    def _best_candidate(self, candidates: list[SymbolInfo]) -> SymbolInfo | None:
        """Pick the best symbol from candidates: callable > class > other, longest name wins ties."""
        callables = [c for c in candidates if self._adapter.is_callable(c.kind)]
        if callables:
            return max(callables, key=lambda c: len(c.qualified_name))
        classes = [c for c in candidates if self._adapter.is_class_like(c.kind)]
        if classes:
            return max(classes, key=lambda c: len(c.qualified_name))
        return max(candidates, key=lambda c: len(c.qualified_name)) if candidates else None

    def _build_package_deps(self, edge_set: set[tuple[str, str]], source_files: list[Path]) -> dict[str, dict]:
        """Phase 4: Infer package dependencies from cross-package edges."""
        all_packages = self._adapter.get_all_packages(source_files, self._root)

        package_deps: dict[str, dict] = {}
        for pkg in sorted(all_packages):
            package_deps[pkg] = {"imports": [], "imported_by": []}

        st = self._symbol_table
        for src, dst in edge_set:
            src_sym = st.symbols.get(src)
            dst_sym = st.symbols.get(dst)
            if not src_sym or not dst_sym:
                continue
            src_pkg = self._adapter.get_package_for_file(src_sym.file_path, self._root)
            dst_pkg = self._adapter.get_package_for_file(dst_sym.file_path, self._root)
            if src_pkg != dst_pkg:
                if src_pkg in package_deps and dst_pkg not in package_deps[src_pkg]["imports"]:
                    package_deps[src_pkg]["imports"].append(dst_pkg)
                if dst_pkg in package_deps and src_pkg not in package_deps[dst_pkg]["imported_by"]:
                    package_deps[dst_pkg]["imported_by"].append(src_pkg)

        for pkg_info in package_deps.values():
            pkg_info["imports"].sort()
            pkg_info["imported_by"].sort()

        return package_deps
