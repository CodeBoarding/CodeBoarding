"""Core algorithm for building call flow graphs using LSP."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from static_analyzer.engine.hierarchy_builder import HierarchyBuilder
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.models import CallFlowGraph, EdgeBuildContext, LanguageAnalysisResult
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable

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

        ctx = EdgeBuildContext(self._lsp, self._symbol_table, self._source_inspector)
        edge_set = self._adapter.build_edges(ctx, source_files)
        edge_set = self._postprocess_edges(edge_set)
        t_edges_done = time.monotonic()
        logger.info("Phase 2 total (build edges): %.1fs, %d edges", t_edges_done - t_indices_done, len(edge_set))

        hierarchy_builder = HierarchyBuilder(self._lsp, self._symbol_table, self._source_inspector, self._adapter)
        hierarchy = hierarchy_builder.build()
        t_hierarchy_done = time.monotonic()
        logger.info("Phase 3 (hierarchy): %.1fs", t_hierarchy_done - t_edges_done)

        package_deps = self._build_package_deps(edge_set, source_files)
        t_pkgdeps_done = time.monotonic()
        logger.info("Phase 4 (package deps): %.1fs", t_pkgdeps_done - t_hierarchy_done)

        # Build references from primary symbols only (not unqualified aliases).
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
        if source_files and self._adapter.references_per_query_timeout > 0:
            logger.info("Phase 1.5 (warmup): triggering LSP index build with a single references request...")
            t_warmup = time.monotonic()
            try:
                self._lsp.references(source_files[0], 0, 0)
            except (TimeoutError, Exception) as e:
                logger.warning("Warmup probe failed (non-fatal): %s", e)
            logger.info("Phase 1.5 (warmup): completed in %.1fs", time.monotonic() - t_warmup)

    def _postprocess_edges(self, edge_set: set[tuple[str, str]]) -> set[tuple[str, str]]:
        """Deduplicate edges by definition location and expand constructor edges.

        Shared post-processing applied after any edge-building strategy.
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
