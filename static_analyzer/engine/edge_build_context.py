"""Edge build context — bundles tools needed for edge-building strategies."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from static_analyzer.engine.liveness import Canary
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.models import ExternalCallSite
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable


@dataclass
class EdgeBuildContext:
    """Context passed to edge-building strategies with all tools needed."""

    lsp: LSPClient
    symbol_table: SymbolTable
    source_inspector: SourceInspector
    # Calls the server resolved into files this engine has no symbols for; the
    # merged graph of every engine is where they can still become edges.
    external_call_sites: list[ExternalCallSite] = field(default_factory=list)
    # The first definition the server answered, asked again through every phase that sends
    # definition requests and once more after the last of them.
    definitions: Canary[tuple[Path, int, int]] = field(init=False)

    def __post_init__(self) -> None:
        self.definitions = Canary(
            "Phase 2 (definitions)", lambda query: bool(self.lsp.send_definition_batch([query])[0])
        )
