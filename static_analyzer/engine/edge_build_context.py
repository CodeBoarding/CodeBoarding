"""Edge build context — bundles tools needed for edge-building strategies."""

from __future__ import annotations

from dataclasses import dataclass

from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable


@dataclass
class EdgeBuildContext:
    """Context passed to edge-building strategies with all tools needed."""

    lsp: LSPClient
    symbol_table: SymbolTable
    source_inspector: SourceInspector
