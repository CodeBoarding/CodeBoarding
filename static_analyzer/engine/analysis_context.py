"""Analysis-scoped symbol ownership, shared by full and incremental engine sessions."""

from pathlib import Path
from threading import RLock, local

from static_analyzer.config import Language
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.models import SymbolInfo
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable


class AnalysisContext:
    """Own lossless language partitions; engine sessions own only their LSP clients."""

    def __init__(self) -> None:
        self.lock = RLock()
        self._inspectors = local()
        self.tables: dict[Language, SymbolTable] = {}
        self.prepared: dict[tuple[Language, Path], list[Path]] = {}
        self.closed_documents: set[Path] = set()
        self.unresolved_files: set[str] = set()

    @property
    def source_inspector(self) -> SourceInspector:
        """Tree-sitter parsers stay on their worker; semantic symbols are shared."""
        if not hasattr(self._inspectors, "value"):
            self._inspectors.value = SourceInspector()
        return self._inspectors.value

    def symbols_for(self, adapter: LanguageAdapter) -> SymbolTable:
        with self.lock:
            language = adapter.results_language
            if language not in self.tables:
                self.tables[language] = SymbolTable(adapter)
            return self.tables[language]

    def hydrate(
        self,
        adapter: LanguageAdapter,
        symbols: list[SymbolInfo],
        unresolved_files: set[str],
        closed_documents: set[str],
    ) -> None:
        """Restore declarations and the source-query state omitted from output graphs."""
        self.symbols_for(adapter).add_symbols(symbols)
        self.unresolved_files.update(unresolved_files)
        self.closed_documents.update(Path(path) for path in closed_documents)

    def freeze(self) -> None:
        """Build derived indices after every engine has registered its declarations."""
        for table in self.tables.values():
            table.build_indices()
