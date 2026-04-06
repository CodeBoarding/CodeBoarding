"""Core algorithm for building call flow graphs using LSP."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from static_analyzer.engine.edge_build_context import EdgeBuildContext
from static_analyzer.engine.edge_builder import build_edges_via_definitions, build_edges_via_references
from static_analyzer.engine.progress import ProgressLogger
from static_analyzer.engine.hierarchy_builder import HierarchyBuilder
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.lsp_constants import DID_OPEN_BATCH_SIZE, EdgeStrategy
from static_analyzer.engine.models import CallFlowGraph, LanguageAnalysisResult
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable

logger = logging.getLogger(__name__)


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

    def build(self, source_files: list[Path], skip_hierarchy: bool = True) -> LanguageAnalysisResult:
        """Run the full analysis pipeline and return results.

        Args:
            source_files: List of source files to analyze.
            skip_hierarchy: If True (default), skip Phase 3 (class hierarchy).
                Hierarchy is currently not consumed by the LLM agents.
        """
        t_pipeline = time.monotonic()

        self._discover_symbols(source_files)
        t_symbols_done = time.monotonic()
        logger.info("Phase 1 total (discover symbols): %.1fs", t_symbols_done - t_pipeline)

        self._symbol_table.build_indices()
        t_indices_done = time.monotonic()
        logger.info("Build indices: %.1fs", t_indices_done - t_symbols_done)

        ctx = EdgeBuildContext(self._lsp, self._symbol_table, self._source_inspector)
        edge_set = self._build_edges(ctx, source_files)
        edge_set = self._postprocess_edges(edge_set)
        t_edges_done = time.monotonic()
        logger.info("Phase 2 total (build edges): %.1fs, %d edges", t_edges_done - t_indices_done, len(edge_set))

        next_phase = 3
        hierarchy: dict[str, dict] = {}
        if skip_hierarchy:
            logger.info("Phase %d (hierarchy): skipped", next_phase)
        else:
            hierarchy_builder = HierarchyBuilder(self._lsp, self._symbol_table, self._source_inspector, self._adapter)
            hierarchy = hierarchy_builder.build()
            logger.info("Phase %d (hierarchy): %.1fs", next_phase, time.monotonic() - t_edges_done)
            next_phase += 1

        t_pre_pkgdeps = time.monotonic()
        package_deps = self._build_package_deps(edge_set, source_files)
        logger.info("Phase %d (package deps): %.1fs", next_phase, time.monotonic() - t_pre_pkgdeps)

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
                    "line": sym.start_line + 1,
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

    def _build_edges(self, ctx: EdgeBuildContext, source_files: list[Path]) -> set[tuple[str, str]]:
        """Dispatch to the edge-building strategy specified by the adapter."""
        if self._adapter.edge_strategy == EdgeStrategy.DEFINITIONS:
            return build_edges_via_definitions(self._adapter, ctx, source_files)
        return build_edges_via_references(self._adapter, ctx, source_files)

    def _discover_symbols(self, source_files: list[Path]) -> None:
        """Phase 0+1: Open files, wait for indexing, then extract all symbols."""
        total = len(source_files)

        # Synchronization probe — blocks until the LSP server has indexed
        # the project. Scales linearly with file count (no OS branching);
        # the per-file ceiling is picked conservatively to cover gopls on
        # AV-heavy Windows and APFS macOS without being loose enough to
        # let a hung LSP waste CI minutes. Adapters can raise the floor via
        # ``get_probe_timeout_minimum()`` (e.g. csharp-ls needs extra time
        # to load a Roslyn workspace).
        _PROBE_STARTUP_BASE = 60  # seconds — LSP startup independent of file count
        _PROBE_PER_FILE = 2.0  # seconds — ceiling across OSes (Linux observed ~0.35s/file)
        _PROBE_MAX_TIMEOUT = 1800  # seconds — hard cap
        probe_timeout = int(min(_PROBE_STARTUP_BASE + total * _PROBE_PER_FILE, _PROBE_MAX_TIMEOUT))
        probe_timeout = max(probe_timeout, self._adapter.get_probe_timeout_minimum())

        # Phase 0 (didOpen) and the sync probe were originally inline here.
        # Extracted to _bulk_did_open / _send_sync_probe so the order can
        # flip: workspace-based servers (e.g., csharp-ls) need the probe
        # BEFORE didOpen because bulk notifications overwhelm them during
        # workspace loading.
        if self._adapter.probe_before_open:
            probe_result = self._send_sync_probe(source_files, probe_timeout)
            self._bulk_did_open(source_files)
        else:
            self._bulk_did_open(source_files)
            probe_result = self._send_sync_probe(source_files, probe_timeout)

        # Phase 1: extract symbols from each file
        pbar = ProgressLogger("Phase 1 (symbols)", total, unit="file")
        for idx, file_path in enumerate(source_files, 1):
            # Reuse the sync probe result for the first file to avoid a
            # redundant document_symbol query (the probe can take minutes).
            if idx == 1 and probe_result is not None:
                symbols = probe_result
            else:
                symbols = self._lsp.document_symbol(file_path)
            self._symbol_table.register_symbols(file_path, symbols, parent_chain=[], project_root=self._root)
            pbar.set_postfix(symbols=len(self._symbol_table.symbols))
            pbar.update(1)
        pbar.finish()

        logger.info("Discovered %d symbols across %d files", len(self._symbol_table.symbols), len(source_files))

        self._warmup_references(source_files)

    def _bulk_did_open(self, source_files: list[Path]) -> None:
        """Phase 0: Send didOpen for all files so the LSP server can index them."""
        total = len(source_files)
        t_open_start = time.monotonic()
        pbar = ProgressLogger("Phase 0 (open)", total, unit="file")
        for i in range(0, total, DID_OPEN_BATCH_SIZE):
            batch = source_files[i : i + DID_OPEN_BATCH_SIZE]
            for file_path in batch:
                self._lsp.did_open(file_path, self._adapter.language_id)
            pbar.update(len(batch))
            time.sleep(0.1)
        pbar.finish()
        logger.info("did_open %d files: %.1fs", total, time.monotonic() - t_open_start)

    def _send_sync_probe(self, source_files: list[Path], probe_timeout: int) -> list[dict] | None:
        """Send a documentSymbol probe to wait for the LSP server to finish indexing."""
        probe_result: list[dict] | None = None
        logger.info("Waiting for LSP server indexing (timeout=%ds)...", probe_timeout)
        t_probe = time.monotonic()
        if source_files:
            probe_result = self._lsp.document_symbol(source_files[0], timeout=probe_timeout)
        logger.info(
            "Sync probe: %.1fs (%d symbols)",
            time.monotonic() - t_probe,
            len(probe_result) if probe_result else 0,
        )
        return probe_result

    def _warmup_references(self, source_files: list[Path]) -> None:
        """Trigger the LSP server's cross-reference index build.

        Sends a single references request with a long timeout so that the
        server builds its index before we send batched queries in Phase 2.
        Only relevant for adapters that use references-based edge building.
        """
        if not source_files or self._adapter.references_per_query_timeout <= 0:
            return
        logger.info("Phase 1.5 (warmup): triggering LSP index build with a single references request...")
        t_warmup = time.monotonic()
        try:
            self._lsp.references(source_files[0], 0, 0)
        except Exception as e:
            logger.warning("Warmup probe failed (non-fatal): %s", e)
        logger.info("Phase 1.5 (warmup): completed in %.1fs", time.monotonic() - t_warmup)

    def _postprocess_edges(self, edge_set: set[tuple[str, str]]) -> set[tuple[str, str]]:
        """Deduplicate edges by definition location and expand constructor edges.

        Dual registration creates multiple qualified names for the same symbol
        at the same source position (e.g. ``module.Class.method`` and
        ``module.method``). Without dedup, we'd get duplicate edges like
        ``(A -> module.Class.method)`` AND ``(A -> module.method)``.

        This method:
        1. Groups edges by (src_position, dst_position) to collapse aliases.
        2. Keeps the edge with the longest qualified names (most specific form).
        3. Removes "alias self-edges" where src and dst are different qualified
           names but resolve to the same definition location.
        4. Expands class edges to include constructor edges.
        """
        st = self._symbol_table

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
                # Replace with the longest qualified names (most specific form,
                # e.g. "module.Class.method" wins over "module.method").
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
