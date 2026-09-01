"""Symbol storage, registration, and lookup for LSP-based analysis."""

from __future__ import annotations

import logging
from pathlib import Path

from static_analyzer.engine.protocols import SymbolNaming
from static_analyzer.config import ANONYMOUS_SYMBOL_MARKERS, NodeType
from static_analyzer.engine.lsp_constants import CALLABLE_KINDS
from static_analyzer.engine.models import SymbolInfo

logger = logging.getLogger(__name__)

BUILD_ROOT_DIRS = frozenset(
    {"src", "lib", "source", "sources", "packages", "apps", "modules", "pkg", "main", "java", "test", "tests"}
)
"""Directories that name a build layout rather than the code, so they distinguish nothing."""

MAX_ORIGIN_SEGMENTS = 3
"""Enough of the path to separate two declarations without restating the whole tree."""


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
    def class_to_ctors(self) -> dict[str, list[str]]:
        """Class qualified name -> the qualified names of its constructors."""
        return self._class_to_ctors

    @property
    def primary_file_symbols(self) -> dict[str, list[SymbolInfo]]:
        """Primary symbols per file (no dual-registration aliases)."""
        return self._primary_file_symbols

    @property
    def file_symbols(self) -> dict[str, list[SymbolInfo]]:
        """All symbols per file (including aliases)."""
        return self._file_symbols

    def register_symbols(
        self,
        file_path: Path,
        symbols: list[dict],
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        owner_qualified_name: str = "",
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
                promoted_from_variable=promoted,
            )
            info.parent_chain = list(parent_chain)
            info.owner_qualified_name = owner_qualified_name

            self._symbols[qualified_name] = info
            ref_key = self._naming.build_reference_key(qualified_name)
            self._ref_key_to_symbol[ref_key] = info
            self._file_symbols.setdefault(file_key, []).append(info)
            self._primary_file_symbols.setdefault(file_key, []).append(info)

            # Dual registration: register unqualified form(s) for symbols with parents
            # Aliases go into _file_symbols but NOT _primary_file_symbols
            if parent_chain:
                # An alias drops the declaring types but stays in the scope that declares it.
                # Why: a C# file may declare several namespaces, and an alias built from an
                # empty chain cannot say which, so the adapter would fall back to the
                # directory -- putting a directory-prefixed alias beside a namespace-prefixed
                # primary. Definition lookup then picks whichever string is longer.
                scope_chain = [(n, k) for n, k in parent_chain if k in (NodeType.NAMESPACE, NodeType.PACKAGE)]
                unqualified_name = self._naming.build_qualified_name(
                    file_path, name, kind, scope_chain, project_root, detail
                )
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
                        promoted_from_variable=promoted,
                    )
                    unq_info.parent_chain = []
                    self._symbols[unqualified_name] = unq_info
                    unq_ref_key = self._naming.build_reference_key(unqualified_name)
                    self._ref_key_to_symbol[unq_ref_key] = unq_info
                    self._file_symbols[file_key].append(unq_info)

                if len(parent_chain) >= 2:
                    for skip in range(1, len(parent_chain)):
                        partial_chain = parent_chain[skip:]
                        # Same reason: keep the declaring scope on every partial form.
                        scoped_partial = [entry for entry in scope_chain if entry not in partial_chain] + list(
                            partial_chain
                        )
                        partial_name = self._naming.build_qualified_name(
                            file_path, name, kind, scoped_partial, project_root, detail
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
                                promoted_from_variable=promoted,
                            )
                            p_info.parent_chain = list(partial_chain)
                            self._symbols[partial_name] = p_info
                            p_ref_key = self._naming.build_reference_key(partial_name)
                            self._ref_key_to_symbol[p_ref_key] = p_info
                            self._file_symbols[file_key].append(p_info)

            children = sym.get("children", [])
            if children:
                # A namespace carries its full dotted name in `detail`, while its own symbol
                # name is only the last segment. Push the full one so a child can resolve its
                # scope from the chain alone: two namespaces in one file may end in the same
                # segment, and then the segment does not identify either.
                chain_name = detail if kind in (NodeType.NAMESPACE, NodeType.PACKAGE) and detail else name
                child_chain = parent_chain + [(chain_name, kind)]
                self.register_symbols(file_path, children, child_chain, project_root, qualified_name)

    def build_indices(self) -> None:
        """Build optimized lookup indices after symbol registration.

        Called once after all symbols are registered. Provides O(1)
        name-based equivalent lookups and class-to-constructor mappings.
        """
        self._separate_contested_declarations()

        # Build (file, name) -> symbols index for equivalent name lookup
        for file_key, syms in self._file_symbols.items():
            for sym in syms:
                idx_key = (file_key, sym.name)
                self._file_name_index.setdefault(idx_key, []).append(sym)

        # Class -> constructors, keyed on the declaring symbol recorded during the walk.
        # Why not a slice of the qualified name at its first "(": that agrees with the real
        # class only by luck of the naming scheme, and it cannot tell a primary symbol from
        # a dual-registration alias, so it indexed a second node for the same constructor.
        for sym in (s for syms in self._primary_file_symbols.values() for s in syms):
            if sym.kind == NodeType.CONSTRUCTOR and sym.owner_qualified_name:
                self._class_to_ctors.setdefault(sym.owner_qualified_name, []).append(sym.qualified_name)

    def _separate_contested_declarations(self) -> None:
        """Give each file its own name where two files declare the same one.

        Two files may legally declare the same fully-qualified type: one C# namespace across
        platform folders, or the same namespace in two projects. The compiler tells them
        apart by assembly; without one, the later registration replaced the earlier and took
        its edges with it.

        The declaring directory goes in front, not behind, because the qualified name is not
        just a label -- ``result_converter`` finds a member's class by joining dot-separated
        prefixes of its name. A suffix leaves ``N.C.M @ dir`` searching for ``N.C`` while the
        class is ``N.C @ dir``, and every member loses its containment edge. A leading
        segment is just another segment, so the arithmetic still holds.

        Run after every file is registered, so the outcome does not depend on which file the
        walk reached first, and descendants move with the declaration that owns them.
        """
        claimants: dict[str, set[Path]] = {}
        for sym in (s for syms in self._primary_file_symbols.values() for s in syms):
            # A namespace or package is one scope many files declare, not rival declarations.
            # Counting it made every file in a namespace contest it, and the descendant match
            # below then moved that whole namespace under a directory.
            if sym.kind in (NodeType.NAMESPACE, NodeType.PACKAGE):
                continue
            claimants.setdefault(sym.qualified_name, set()).add(sym.file_path)
        contested = {name for name, files in claimants.items() if len(files) > 1}
        if not contested:
            return

        renamed: dict[tuple[str, str], str] = {}
        for file_key, syms in self._primary_file_symbols.items():
            for sym in syms:
                owner = next(
                    (c for c in contested if sym.qualified_name == c or sym.qualified_name.startswith(f"{c}.")), None
                )
                if owner is None:
                    continue
                renamed[(file_key, sym.qualified_name)] = f"{self._origin_of(sym)}.{sym.qualified_name}"

        for (file_key, old_name), new_name in renamed.items():
            for sym in self._file_symbols.get(file_key, []):
                if sym.qualified_name != old_name:
                    continue
                self._symbols.pop(old_name, None)
                sym.qualified_name = new_name
                self._symbols[new_name] = sym
                self._ref_key_to_symbol[self._naming.build_reference_key(new_name)] = sym
            for sym in self._primary_file_symbols.get(file_key, []):
                if sym.owner_qualified_name == old_name:
                    sym.owner_qualified_name = new_name

        logger.info(
            "[Naming] %d name(s) declared by more than one file; %d symbol(s) moved under their origin",
            len(contested),
            len(renamed),
        )

    def _origin_of(self, sym: SymbolInfo) -> str:
        """The declaring directory, as dotted segments, with build roots dropped."""
        parts = [p for p in sym.file_path.parent.parts if p not in BUILD_ROOT_DIRS]
        return ".".join(parts[-MAX_ORIGIN_SEGMENTS:]) if parts else sym.file_path.stem

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
