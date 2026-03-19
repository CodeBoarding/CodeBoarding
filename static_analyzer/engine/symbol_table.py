"""Symbol storage, registration, and lookup for LSP-based analysis."""

from __future__ import annotations

import logging
from pathlib import Path

from static_analyzer.engine.protocols import SymbolNaming
from static_analyzer.constants import NodeType
from static_analyzer.engine.lsp_constants import CALLABLE_KINDS
from static_analyzer.engine.models import SymbolInfo

logger = logging.getLogger(__name__)


class SymbolTable:
    """Manages symbol discovery, registration, and lookup.

    Owns all symbol dictionaries and provides methods for querying
    symbols by name, position, or qualified name.
    """

    def __init__(self, naming: SymbolNaming) -> None:
        self._naming = naming

        # Symbol table: qualified_name -> SymbolInfo
        self._symbols: dict[str, SymbolInfo] = {}
        # File -> list of ALL symbols in that file (including aliases)
        self._file_symbols: dict[str, list[SymbolInfo]] = {}
        # File -> list of PRIMARY symbols only (no aliases, for containment/lift)
        self._primary_file_symbols: dict[str, list[SymbolInfo]] = {}
        # Reference key (lowercase) -> symbol info
        self._ref_key_to_symbol: dict[str, SymbolInfo] = {}

        # --- Lookup indices built after registration ---
        # (file_key, name) -> list of symbols with that name in that file
        self._file_name_index: dict[tuple[str, str], list[SymbolInfo]] = {}
        # class qualified_name -> list of constructor qualified_names
        self._class_to_ctors: dict[str, list[str]] = {}

    @property
    def symbols(self) -> dict[str, SymbolInfo]:
        """Public read-only access to the symbol table."""
        return self._symbols

    @property
    def primary_file_symbols(self) -> dict[str, list[SymbolInfo]]:
        """Primary symbols per file (no dual-registration aliases)."""
        return self._primary_file_symbols

    @property
    def file_symbols(self) -> dict[str, list[SymbolInfo]]:
        """All symbols per file (including aliases)."""
        return self._file_symbols

    @property
    def class_to_ctors(self) -> dict[str, list[str]]:
        """Class qualified name -> list of constructor qualified names."""
        return self._class_to_ctors

    def register_symbols(
        self,
        file_path: Path,
        symbols: list[dict],
        parent_chain: list[tuple[str, int]],
        project_root: Path,
    ) -> None:
        """Recursively register symbols with dual registration."""
        for sym in symbols:
            name = sym.get("name", "")
            kind = sym.get("kind", 0)
            detail = sym.get("detail", "")

            if not name:
                continue

            # Promote variables/constants with method children to class
            children = sym.get("children", [])
            if kind in (NodeType.VARIABLE, NodeType.CONSTANT) and children:
                child_kinds = {c.get("kind", 0) for c in children}
                if child_kinds & CALLABLE_KINDS:
                    kind = NodeType.CLASS

            range_info = sym.get("range", sym.get("location", {}).get("range", {}))
            sel_range = sym.get("selectionRange", range_info)

            start = range_info.get("start", {})
            end = range_info.get("end", {})
            sel_start = sel_range.get("start", start)

            start_line = sel_start.get("line", 0)
            start_char = sel_start.get("character", 0)
            end_line = end.get("line", 0)
            end_char = end.get("character", 0)

            file_key = str(file_path)

            qualified_name = self._naming.build_qualified_name(
                file_path, name, kind, parent_chain, project_root, detail
            )

            info = SymbolInfo(
                name=name,
                qualified_name=qualified_name,
                kind=kind,
                file_path=file_path,
                start_line=start_line,
                start_char=start_char,
                end_line=end_line,
                end_char=end_char,
            )
            info.parent_chain = list(parent_chain)

            self._symbols[qualified_name] = info
            ref_key = self._naming.build_reference_key(qualified_name)
            self._ref_key_to_symbol[ref_key] = info
            self._file_symbols.setdefault(file_key, []).append(info)
            self._primary_file_symbols.setdefault(file_key, []).append(info)

            # Dual registration: register unqualified form(s) for symbols with parents
            # Aliases go into _file_symbols but NOT _primary_file_symbols
            if parent_chain:
                unqualified_name = self._naming.build_qualified_name(file_path, name, kind, [], project_root, detail)
                if unqualified_name != qualified_name and unqualified_name not in self._symbols:
                    unq_info = SymbolInfo(
                        name=name,
                        qualified_name=unqualified_name,
                        kind=kind,
                        file_path=file_path,
                        start_line=start_line,
                        start_char=start_char,
                        end_line=end_line,
                        end_char=end_char,
                    )
                    unq_info.parent_chain = []
                    self._symbols[unqualified_name] = unq_info
                    unq_ref_key = self._naming.build_reference_key(unqualified_name)
                    self._ref_key_to_symbol[unq_ref_key] = unq_info
                    self._file_symbols[file_key].append(unq_info)

                if len(parent_chain) >= 2:
                    for skip in range(1, len(parent_chain)):
                        partial_chain = parent_chain[skip:]
                        partial_name = self._naming.build_qualified_name(
                            file_path, name, kind, partial_chain, project_root, detail
                        )
                        if partial_name != qualified_name and partial_name not in self._symbols:
                            p_info = SymbolInfo(
                                name=name,
                                qualified_name=partial_name,
                                kind=kind,
                                file_path=file_path,
                                start_line=start_line,
                                start_char=start_char,
                                end_line=end_line,
                                end_char=end_char,
                            )
                            p_info.parent_chain = list(partial_chain)
                            self._symbols[partial_name] = p_info
                            p_ref_key = self._naming.build_reference_key(partial_name)
                            self._ref_key_to_symbol[p_ref_key] = p_info
                            self._file_symbols[file_key].append(p_info)

            children = sym.get("children", [])
            if children:
                child_chain = parent_chain + [(name, kind)]
                self.register_symbols(file_path, children, child_chain, project_root)

    def build_indices(self) -> None:
        """Build optimized lookup indices after symbol registration.

        Called once after all symbols are registered. Provides O(1)
        name-based equivalent lookups and class-to-constructor mappings.
        """
        # Build (file, name) -> symbols index for equivalent name lookup
        for file_key, syms in self._file_symbols.items():
            for sym in syms:
                idx_key = (file_key, sym.name)
                self._file_name_index.setdefault(idx_key, []).append(sym)

        # Build class -> constructor index
        for qname, sym in self._symbols.items():
            if sym.kind == NodeType.CONSTRUCTOR:
                # Find the class this constructor belongs to by stripping the params
                paren_idx = qname.find("(")
                if paren_idx != -1:
                    class_qname = qname[:paren_idx]
                    self._class_to_ctors.setdefault(class_qname, []).append(qname)

    def find_containing_symbol(self, file_path: Path, line: int, character: int) -> SymbolInfo | None:
        """Find the innermost symbol whose range contains the given position.

        When the best match is a class-like symbol and the reference line falls
        in the gap between methods (e.g. on a decorator line), narrow the result
        to the nearest child method whose definition starts just after the
        reference line.  This correctly attributes decorator references like
        ``@trace`` to the decorated method rather than the enclosing class.
        """
        file_key = str(file_path)
        symbols = self._file_symbols.get(file_key, [])

        best: SymbolInfo | None = None
        best_size = float("inf")

        for sym in symbols:
            if sym.start_line <= line <= sym.end_line:
                if sym.start_line == line and character < sym.start_char:
                    continue
                if sym.end_line == line and character > sym.end_char:
                    continue
                size = (sym.end_line - sym.start_line) * 10000 + (sym.end_char - sym.start_char)
                if size < best_size or (
                    size == best_size and best is not None and len(sym.qualified_name) > len(best.qualified_name)
                ):
                    best = sym
                    best_size = size

        # If the best match is a class-like symbol, check if the reference line
        # is actually a decorator/annotation for one of its child methods.
        # This heuristic works across languages: Python decorators (@trace),
        # Java annotations (@Override, @Inject), TypeScript decorators (@Component).
        # These sit 1-3 lines before the method definition line (accounting for
        # stacked decorators/annotations).  Attribute the reference to the
        # nearest child method whose start_line is within a small window.
        if best and self._naming.is_class_like(best.kind):
            max_decorator_gap = 4
            nearest_child: SymbolInfo | None = None
            nearest_gap = max_decorator_gap + 1
            for sym in symbols:
                if not self._naming.is_callable(sym.kind):
                    continue
                if not sym.qualified_name.startswith(best.qualified_name + "."):
                    continue
                gap = sym.start_line - line
                if 0 < gap < nearest_gap:
                    nearest_child = sym
                    nearest_gap = gap
            if nearest_child is not None:
                best = nearest_child

        return best

    def lift_to_callable(self, sym: SymbolInfo) -> SymbolInfo | None:
        """If sym is a variable/property, find its parent callable symbol."""
        if self._naming.is_callable(sym.kind) or self._naming.is_class_like(sym.kind):
            return sym

        file_key = str(sym.file_path)
        candidates = self._file_symbols.get(file_key, [])

        best: SymbolInfo | None = None
        best_size = float("inf")

        for other in candidates:
            if other.qualified_name == sym.qualified_name:
                continue
            if not (self._naming.is_callable(other.kind) or self._naming.is_class_like(other.kind)):
                continue
            if other.start_line <= sym.start_line and other.end_line >= sym.end_line:
                size = (other.end_line - other.start_line) * 10000 + (other.end_char - other.start_char)
                if size < best_size:
                    best = other
                    best_size = size

        return best or sym

    def get_equivalent_names(self, qualified_name: str) -> list[str]:
        """Get equivalent symbol names for edge expansion using pre-built index."""
        sym = self._symbols.get(qualified_name)
        if sym is None:
            return []

        file_key = str(sym.file_path)
        idx_key = (file_key, sym.name)
        same_name_syms = self._file_name_index.get(idx_key, [])

        return [s.qualified_name for s in same_name_syms if s.qualified_name != qualified_name]

    def get_canonical_name(self, qualified_name: str) -> str:
        """Return the canonical (shortest) qualified name for a symbol.

        Dual registration creates multiple qualified names for the same symbol
        at the same position (e.g. ``Module.Class.method`` and ``Module.method``).
        To avoid edge duplication, we pick the **shortest** form so that every
        equivalent alias maps to the same canonical edge.
        """
        sym = self._symbols.get(qualified_name)
        if sym is None:
            return qualified_name
        equivalents = self.get_equivalent_names(qualified_name)
        if not equivalents:
            return qualified_name
        all_names = [qualified_name] + equivalents
        return min(all_names, key=len)

    def is_local_variable(self, sym: SymbolInfo) -> bool:
        """Check whether a symbol is a local/parameter that should be excluded.

        Excludes:
        - Variables/constants with any parent (parameters, locals, attributes)
        - Properties inside callables (e.g. destructured return values,
          object literal properties in TypeScript/JavaScript functions)

        Module-level variables (handler functions, constants used as callbacks)
        and class-level properties/fields are kept.

        Also catches unqualified aliases (dual registration) by checking if
        any symbol at the same position has a parent.
        """
        if sym.kind in (NodeType.VARIABLE, NodeType.CONSTANT):
            if sym.parent_chain:
                return True
            # Check if any co-located symbol (alias at same position) has a parent
            pos_key = sym.definition_location
            file_key = str(sym.file_path)
            for other in self._file_symbols.get(file_key, []):
                if other.definition_location == pos_key and other.parent_chain:
                    return True
            return False

        if sym.kind == NodeType.PROPERTY and sym.parent_chain:
            # Properties inside callables are local (e.g. destructured values);
            # properties whose immediate parent is a class are class members — keep those.
            parent_kind = sym.parent_chain[-1][1] if sym.parent_chain else 0
            if self._naming.is_callable(parent_kind):
                return True

        return False
