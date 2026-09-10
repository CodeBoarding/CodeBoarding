"""Symbol storage, registration, and lookup for LSP-based analysis."""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path

from static_analyzer.engine.protocols import SymbolNaming
from static_analyzer.config import ANONYMOUS_SYMBOL_MARKERS, NodeType
from static_analyzer.engine.lsp_constants import CALLABLE_KINDS, CLASS_LIKE_KINDS
from static_analyzer.engine.models import SymbolInfo
from static_analyzer.engine.utils import definition_location

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
        self._definition_positions: dict[tuple[str, int, int], SymbolInfo] = {}
        self._definition_lines: dict[tuple[str, int], list[SymbolInfo]] = {}
        self._indices_dirty = False

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
        owner_qualified_name: str = "",
        naming: SymbolNaming | None = None,
    ) -> None:
        """Recursively register symbols with dual registration."""
        naming = naming or self._naming
        for sym in symbols:
            name = sym.get("name", "")
            kind = sym.get("kind", 0)
            detail = sym.get("detail", "")

            if not name:
                continue

            # Promote variables/constants with method children to class
            children = sym.get("children", [])
            promoted = False
            if kind in (NodeType.VARIABLE, NodeType.CONSTANT) and children:
                child_kinds = {c.get("kind", 0) for c in children}
                if child_kinds & CALLABLE_KINDS:
                    kind = NodeType.CLASS
                    promoted = True

            range_info = sym.get("range", sym.get("location", {}).get("range", {}))
            sel_range = sym.get("selectionRange", range_info)

            start = range_info.get("start", {})
            end = range_info.get("end", {})
            sel_start = sel_range.get("start", start)

            start_line = sel_start.get("line", 0)
            start_char = sel_start.get("character", 0)
            end_line = end.get("line", 0)
            end_char = end.get("character", 0)

            qualified_name = naming.build_qualified_name(file_path, name, kind, parent_chain, project_root, detail)

            info = SymbolInfo(
                name=name,
                qualified_name=qualified_name,
                kind=kind,
                file_path=file_path,
                start_line=start_line,
                start_char=start_char,
                end_line=end_line,
                end_char=end_char,
                promoted_from_variable=promoted,
            )
            info.parent_chain = list(parent_chain)
            info.owner_qualified_name = owner_qualified_name

            registrations = [info]

            # Dual registration: register unqualified form(s) for symbols with parents
            # Aliases go into _file_symbols but NOT _primary_file_symbols
            if parent_chain:
                unqualified_name = naming.build_qualified_name(file_path, name, kind, [], project_root, detail)
                if unqualified_name != qualified_name:
                    unq_info = SymbolInfo(
                        name=name,
                        qualified_name=unqualified_name,
                        kind=kind,
                        file_path=file_path,
                        start_line=start_line,
                        start_char=start_char,
                        end_line=end_line,
                        end_char=end_char,
                        promoted_from_variable=promoted,
                        is_primary=False,
                    )
                    unq_info.parent_chain = []
                    registrations.append(unq_info)

                if len(parent_chain) >= 2:
                    for skip in range(1, len(parent_chain)):
                        partial_chain = parent_chain[skip:]
                        partial_name = naming.build_qualified_name(
                            file_path, name, kind, partial_chain, project_root, detail
                        )
                        if partial_name != qualified_name:
                            p_info = SymbolInfo(
                                name=name,
                                qualified_name=partial_name,
                                kind=kind,
                                file_path=file_path,
                                start_line=start_line,
                                start_char=start_char,
                                end_line=end_line,
                                end_char=end_char,
                                promoted_from_variable=promoted,
                                is_primary=False,
                            )
                            p_info.parent_chain = list(partial_chain)
                            registrations.append(p_info)

            self.add_symbols(registrations)
            children = sym.get("children", [])
            if children:
                child_chain = parent_chain + [(name, kind)]
                self.register_symbols(file_path, children, child_chain, project_root, qualified_name, naming)

    def add_symbols(self, symbols: list[SymbolInfo]) -> None:
        """Merge declarations and aliases, choosing collisions by declaration location."""
        for sym in symbols:
            existing = self._symbols.get(sym.qualified_name)
            if existing is not None:
                if self._registration_key(existing) <= self._registration_key(sym):
                    continue
                file_key = str(existing.file_path)
                self._file_symbols[file_key].remove(existing)
                if not self._file_symbols[file_key]:
                    del self._file_symbols[file_key]
                if existing.is_primary:
                    self._primary_file_symbols[file_key].remove(existing)
                    if not self._primary_file_symbols[file_key]:
                        del self._primary_file_symbols[file_key]
            self._symbols[sym.qualified_name] = sym
            file_key = str(sym.file_path)
            self._file_symbols.setdefault(file_key, []).append(sym)
            if sym.is_primary:
                self._primary_file_symbols.setdefault(file_key, []).append(sym)
            ref_key = self._naming.build_reference_key(sym.qualified_name)
            ref_symbol = self._ref_key_to_symbol.get(ref_key)
            if ref_symbol is None or self._registration_key(sym) < self._registration_key(ref_symbol):
                self._ref_key_to_symbol[ref_key] = sym
            self._indices_dirty = True

    def remove_files(self, files: set[Path]) -> None:
        """Remove declarations from changed or deleted files and refresh all lookups."""
        retained = [sym for sym in self._symbols.values() if sym.file_path not in files]
        self._symbols.clear()
        self._file_symbols.clear()
        self._primary_file_symbols.clear()
        self._ref_key_to_symbol.clear()
        self.add_symbols(retained)
        self.build_indices()

    def snapshot(self) -> list[SymbolInfo]:
        """Return detached, deterministic declarations and aliases without naming adapters."""
        return deepcopy(sorted(self._symbols.values(), key=self._registration_key))

    def resolve_definition(self, definition: dict) -> SymbolInfo | None:
        """Resolve an LSP definition by exact position, then callable-first nearby lines."""
        if self._indices_dirty:
            self.build_indices()
        location = definition_location(definition)
        if location is None:
            return None
        file_path, line, character = location
        file_key = str(file_path)
        exact = self._definition_positions.get((file_key, line, character))
        if exact is not None:
            return exact
        for delta in (0, 1, -1, 2, -2):
            candidates = self._definition_lines.get((file_key, line + delta), [])
            if candidates:
                return max(
                    candidates,
                    key=lambda sym: (
                        2 if sym.kind in CALLABLE_KINDS else 1 if sym.kind in CLASS_LIKE_KINDS else 0,
                        len(sym.qualified_name),
                    ),
                )
        return None

    def build_indices(self) -> None:
        """Rebuild name, constructor, and definition indices from registered symbols."""
        self._file_name_index.clear()
        self._class_to_ctors.clear()
        self._definition_positions.clear()
        self._definition_lines.clear()
        for sym in sorted(self._symbols.values(), key=self._registration_key):
            position = sym.definition_location
            existing = self._definition_positions.get(position)
            if existing is None or len(sym.qualified_name) > len(existing.qualified_name):
                self._definition_positions[position] = sym
            self._definition_lines.setdefault((str(sym.file_path), sym.start_line), []).append(sym)

        # Build (file, name) -> symbols index for equivalent name lookup
        for file_key, syms in self._file_symbols.items():
            syms.sort(key=self._registration_key)
            for sym in syms:
                idx_key = (file_key, sym.name)
                self._file_name_index.setdefault(idx_key, []).append(sym)

        # Class -> constructors, keyed on the declaring symbol.
        # Why not a slice at the first "(": that names the class only where the scheme
        # doubles it, and it cannot tell a primary symbol from an alias.
        for syms in self._primary_file_symbols.values():
            syms.sort(key=self._registration_key)
        for sym in (s for syms in self._primary_file_symbols.values() for s in syms):
            if sym.kind == NodeType.CONSTRUCTOR and sym.owner_qualified_name:
                self._class_to_ctors.setdefault(sym.owner_qualified_name, []).append(sym.qualified_name)
        for constructors in self._class_to_ctors.values():
            constructors.sort()
        self._indices_dirty = False

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

    def attribution_symbol(self, sym: SymbolInfo) -> SymbolInfo:
        """The innermost enclosing declaration a reader would name, or ``sym`` itself.

        Why credit only: the guards need the innermost symbol, widening it there drops call sites.
        """
        if not self._is_unnameable(sym):
            return sym

        best: SymbolInfo | None = None
        best_size = float("inf")
        # A promoted `const x = ...` wrapper is a name a reader recognises, unlike an anonymous
        # callback, so it is a usable credit — but only when nothing named encloses it. Module
        # level is exactly that case: there is no function above it to prefer.
        fallback: SymbolInfo | None = None
        fallback_size = float("inf")
        for other in self._file_symbols.get(str(sym.file_path), []):
            if other.qualified_name == sym.qualified_name or self._is_anonymous(other):
                continue
            if not (self._naming.is_callable(other.kind) or self._naming.is_class_like(other.kind)):
                continue
            if not self._encloses(other, sym):
                continue
            size = (other.end_line - other.start_line) * 10000 + (other.end_char - other.start_char)
            if other.promoted_from_variable:
                if size < fallback_size:
                    fallback = other
                    fallback_size = size
            elif size < best_size:
                best = other
                best_size = size
        return best or fallback or sym

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

    @staticmethod
    def _registration_key(sym: SymbolInfo) -> tuple:
        """Order declarations independently of engine completion order."""
        return (
            *sym.definition_location,
            not sym.is_primary,
            sym.end_line,
            sym.end_char,
            sym.qualified_name,
            sym.name,
            sym.kind,
            sym.parent_chain,
            sym.owner_qualified_name,
            sym.promoted_from_variable,
        )

    def _is_unnameable(self, sym: SymbolInfo) -> bool:
        """Whether this symbol's own name is not one a reader would use as a caller."""
        return sym.promoted_from_variable or self._is_anonymous(sym)

    @staticmethod
    def _is_anonymous(sym: SymbolInfo) -> bool:
        return any(marker in sym.name for marker in ANONYMOUS_SYMBOL_MARKERS)

    @staticmethod
    def _encloses(outer: SymbolInfo, inner: SymbolInfo) -> bool:
        """Whether *outer*'s range fully contains *inner*'s, character bounds included.

        Why characters: several declarations can share a line, and comparing lines alone lets
        an unrelated neighbour win.
        """
        if outer.start_line > inner.start_line or outer.end_line < inner.end_line:
            return False
        if outer.start_line == inner.start_line and outer.start_char > inner.start_char:
            return False
        if outer.end_line == inner.end_line and outer.end_char < inner.end_char:
            return False
        return True
