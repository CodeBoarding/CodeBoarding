"""Class hierarchy construction via LSP type hierarchy or source code inference."""

from __future__ import annotations

import logging
import re
import time

from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient, MethodNotFoundError
from static_analyzer.engine.models import SymbolInfo
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable
from static_analyzer.engine.utils import uri_to_path

logger = logging.getLogger(__name__)


class HierarchyBuilder:
    """Builds class hierarchy using LSP type hierarchy, falling back to source parsing."""

    def __init__(
        self,
        lsp: LSPClient,
        symbol_table: SymbolTable,
        source_inspector: SourceInspector,
        adapter: LanguageAdapter,
    ) -> None:
        self._lsp = lsp
        self._symbol_table = symbol_table
        self._source_inspector = source_inspector
        self._adapter = adapter

    def build(self) -> dict[str, dict]:
        """Build class hierarchy using primary symbols only."""
        t_start = time.monotonic()
        hierarchy: dict[str, dict] = {}
        # Use only primary symbols (not dual-registration aliases) to avoid
        # unqualified duplicates like "module.Address" alongside "module.Class.Address"
        primary_qnames: set[str] = set()
        for syms in self._symbol_table.primary_file_symbols.values():
            for sym in syms:
                primary_qnames.add(sym.qualified_name)
        class_symbols = sorted(
            [
                sym
                for sym in self._symbol_table.symbols.values()
                if self._adapter.is_class_like(sym.kind) and sym.qualified_name in primary_qnames
            ],
            key=lambda s: s.qualified_name,
        )
        class_names = {sym.qualified_name for sym in class_symbols}

        for sym in class_symbols:
            hierarchy[sym.qualified_name] = {"superclasses": [], "subclasses": []}

        logger.info("Hierarchy: %d class symbols to process", len(class_symbols))
        type_hierarchy_supported = False
        for sym in class_symbols:
            try:
                items = self._lsp.type_hierarchy_prepare(sym.file_path, sym.start_line, sym.start_char)
                if not items:
                    continue
                type_hierarchy_supported = True
                item = items[0]

                try:
                    supertypes = self._lsp.type_hierarchy_supertypes(item)
                    for st in supertypes:
                        super_name = self._resolve_type_hierarchy_item(st)
                        if super_name and super_name in hierarchy:
                            if super_name not in hierarchy[sym.qualified_name]["superclasses"]:
                                hierarchy[sym.qualified_name]["superclasses"].append(super_name)
                            if sym.qualified_name not in hierarchy[super_name]["subclasses"]:
                                hierarchy[super_name]["subclasses"].append(sym.qualified_name)
                except MethodNotFoundError:
                    pass
                except Exception as e:
                    logger.debug("Failed to get supertypes for %s: %s", sym.qualified_name, e)

                try:
                    subtypes = self._lsp.type_hierarchy_subtypes(item)
                    for st in subtypes:
                        sub_name = self._resolve_type_hierarchy_item(st)
                        if sub_name and sub_name in hierarchy:
                            if sub_name not in hierarchy[sym.qualified_name]["subclasses"]:
                                hierarchy[sym.qualified_name]["subclasses"].append(sub_name)
                            if sym.qualified_name not in hierarchy[sub_name]["superclasses"]:
                                hierarchy[sub_name]["superclasses"].append(sym.qualified_name)
                except MethodNotFoundError:
                    pass
                except Exception as e:
                    logger.debug("Failed to get subtypes for %s: %s", sym.qualified_name, e)
            except MethodNotFoundError:
                logger.info("Type hierarchy not supported by server, skipping remaining classes")
                break
            except Exception as e:
                logger.debug("Type hierarchy not supported for %s: %s", sym.qualified_name, e)

        if not type_hierarchy_supported:
            logger.info("Type hierarchy not supported, inferring from source code")
            self._infer_hierarchy_from_source(class_symbols, class_names, hierarchy)

        links = sum(len(h["superclasses"]) for h in hierarchy.values())
        logger.info(
            "Hierarchy complete: %d classes, %d inheritance links in %.1fs",
            len(hierarchy),
            links,
            time.monotonic() - t_start,
        )
        return hierarchy

    def _resolve_type_hierarchy_item(self, item: dict) -> str | None:
        """Resolve a type hierarchy item to a qualified name in our symbol table."""
        name = item.get("name", "")
        uri = item.get("uri", "")
        sel_range = item.get("selectionRange", item.get("range", {}))
        start = sel_range.get("start", {})
        line = start.get("line", -1)

        file_path = uri_to_path(uri)
        if file_path is None:
            return None

        file_key = str(file_path)
        for sym in self._symbol_table.file_symbols.get(file_key, []):
            if sym.name == name and sym.start_line == line:
                return sym.qualified_name
            if sym.name == name and abs(sym.start_line - line) <= 1:
                return sym.qualified_name

        for sym in self._symbol_table.file_symbols.get(file_key, []):
            if sym.name == name and self._adapter.is_class_like(sym.kind):
                return sym.qualified_name
        return None

    def _infer_hierarchy_from_source(
        self,
        class_symbols: list[SymbolInfo],
        class_names: set[str],
        hierarchy: dict[str, dict],
    ) -> None:
        """Infer inheritance from source code when type hierarchy LSP is unavailable.

        Parses class definition lines to extract base classes. Handles:
        - Python: ``class Dog(Animal):`` or ``class Duck(Animal, SwimmingMixin):``
        - PHP: ``class Dog extends Animal implements Speakable``
        """
        # Build a name-to-qualified-names index for resolving short class names
        short_name_to_qnames: dict[str, list[str]] = {}
        for qname in class_names:
            short = qname.rsplit(".", 1)[-1]
            short_name_to_qnames.setdefault(short, []).append(qname)

        for sym in class_symbols:
            line = self._source_inspector.get_source_line(sym.file_path, sym.start_line)
            if line is None:
                continue

            # Python: class Name(Base1, Base2):
            match = re.search(r"\bclass\s+\w+\s*\(([^)]+)\)", line)
            if match:
                bases_str = match.group(1)
                for base in bases_str.split(","):
                    base_name = base.strip().split("=")[0].strip()  # skip keyword args like metaclass=...
                    if not base_name or base_name.startswith("metaclass"):
                        continue
                    self._link_hierarchy(sym.qualified_name, base_name, short_name_to_qnames, hierarchy)
                continue

            # PHP: class Name extends Base implements Interface1, Interface2
            extends_match = re.search(r"\bextends\s+([\w\\]+)", line)
            if extends_match:
                base_name = extends_match.group(1).rsplit("\\", 1)[-1]
                self._link_hierarchy(sym.qualified_name, base_name, short_name_to_qnames, hierarchy)

            implements_match = re.search(r"\bimplements\s+(.+?)(?:\s*\{|$)", line)
            if implements_match:
                for iface in implements_match.group(1).split(","):
                    iface_name = iface.strip().rsplit("\\", 1)[-1]
                    if iface_name:
                        self._link_hierarchy(sym.qualified_name, iface_name, short_name_to_qnames, hierarchy)

    def _link_hierarchy(
        self,
        child_qname: str,
        parent_short_name: str,
        short_name_to_qnames: dict[str, list[str]],
        hierarchy: dict[str, dict],
    ) -> None:
        """Link a child class to a parent class in the hierarchy by short name."""
        candidates = short_name_to_qnames.get(parent_short_name, [])
        for parent_qname in candidates:
            if parent_qname == child_qname:
                continue
            if parent_qname in hierarchy:
                if parent_qname not in hierarchy[child_qname]["superclasses"]:
                    hierarchy[child_qname]["superclasses"].append(parent_qname)
                if child_qname not in hierarchy[parent_qname]["subclasses"]:
                    hierarchy[parent_qname]["subclasses"].append(child_qname)
