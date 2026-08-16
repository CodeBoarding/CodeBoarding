"""Source code reading and tree-sitter call-site detection utilities."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Language as TreeSitterLanguage
from tree_sitter import Node as TreeSitterNode
from tree_sitter import Parser, Tree

from static_analyzer.constants import LANGUAGE_EXTENSIONS, Language
from static_analyzer.engine.models import CallSite

import tree_sitter_c_sharp
import tree_sitter_go
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_php
import tree_sitter_python
import tree_sitter_rust
import tree_sitter_typescript

logger = logging.getLogger(__name__)


LanguageFactory = Callable[[], object]

_LANGUAGE_FACTORY_BY_LANGUAGE: dict[Language, LanguageFactory] = {
    Language.PYTHON: tree_sitter_python.language,
    Language.JAVASCRIPT: tree_sitter_javascript.language,
    Language.GO: tree_sitter_go.language,
    Language.JAVA: tree_sitter_java.language,
    Language.PHP: tree_sitter_php.language_php,
    Language.RUST: tree_sitter_rust.language,
    Language.CSHARP: tree_sitter_c_sharp.language,
}
_LANGUAGE_BY_SUFFIX: dict[str, LanguageFactory] = {
    suffix: _LANGUAGE_FACTORY_BY_LANGUAGE[language]
    for language, suffixes in LANGUAGE_EXTENSIONS.items()
    if language in _LANGUAGE_FACTORY_BY_LANGUAGE
    for suffix in suffixes
}
_LANGUAGE_BY_SUFFIX[".ts"] = tree_sitter_typescript.language_typescript
_LANGUAGE_BY_SUFFIX[".mts"] = tree_sitter_typescript.language_typescript
_LANGUAGE_BY_SUFFIX[".cts"] = tree_sitter_typescript.language_typescript
_LANGUAGE_BY_SUFFIX[".tsx"] = tree_sitter_typescript.language_tsx

_CALL_NODE_TYPES = frozenset(
    {
        "call",
        "call_expression",
        "function_call_expression",
        "member_call_expression",
        "scoped_call_expression",
        "method_invocation",
        "invocation_expression",
        "explicit_constructor_invocation",
    }
)
_CONSTRUCTOR_NODE_TYPES = frozenset({"object_creation_expression", "new_expression"})
_METHOD_REFERENCE_NODE_TYPES = frozenset({"method_reference"})
_CALLABLE_USAGE_ANCESTORS = frozenset({"argument_list", "arguments"})
_NAME_NODE_TYPES = frozenset(
    {
        "identifier",
        "name",
        "property_identifier",
        "field_identifier",
        "type_identifier",
        "super",
        "this",
    }
)
_GENERIC_TYPE_NODE_TYPES = frozenset({"generic_name", "generic_type"})
_CALL_TARGET_FIELD_NAMES = ("function", "constructor", "name", "field", "property", "attribute")
_CONSTRUCTOR_FIELD_NAMES = ("type", "name")
_ARGUMENT_NODE_TYPES = frozenset({"argument"})
_TYPE_DECLARATION_NODE_TYPES = frozenset(
    {"class_declaration", "interface_declaration", "record_declaration", "struct_declaration"}
)
_BASE_LIST_NODE_TYPES = frozenset({"base_list", "superclass", "super_interfaces", "extends_interfaces"})
# Java groups several bases under one node; C# wraps a record's base in its
# primary-constructor call, whose ``type`` field is the base itself.
_BASE_GROUP_NODE_TYPES = frozenset({"type_list"})
_MEMBER_DECLARATION_NODE_TYPES = frozenset(
    {"method_declaration", "property_declaration", "indexer_declaration", "event_declaration"}
)
_DECLARATION_BLOCK_NODE_TYPES = frozenset({"block", "compound_statement", "statement_block"})
_EXPRESSION_BODY_NODE_TYPES = frozenset({"arrow_expression_clause"})

# Ceiling on retained tree-sitter nodes. Trees are by far the largest thing this
# class touches — retaining one per file cost 2.2GB on a 5k-file C# repo — and
# the common path needs each exactly once, to build that file's usage index,
# which is cached separately and outlives the tree. Measured: going from
# unbounded to 500k costs no wall-clock, because nothing re-reads a tree.
TREE_NODE_BUDGET = 500_000


@dataclass(frozen=True)
class ParsedSource:
    content: bytes
    tree: Tree


@dataclass(frozen=True)
class SourceUsageIndex:
    invocation_end_positions: set[tuple[int, int]]
    callable_ranges: set[tuple[int, int, int]]


class SourceInspector:
    """Reads source files and finds call sites from tree-sitter ASTs."""

    def __init__(self, tree_node_budget: int = TREE_NODE_BUDGET) -> None:
        self._file_content_cache: dict[str, list[str]] = {}
        # LRU, evicted against ``_tree_node_budget``. Ordered so the oldest tree
        # goes first; everything derived from a tree is cached in its own map.
        self._parsed_cache: OrderedDict[str, ParsedSource] = OrderedDict()
        self._parsed_nodes = 0
        self._tree_node_budget = tree_node_budget
        self._trees_evicted = 0
        self._parser_by_suffix: dict[str, Parser] = {}
        self._usage_index_cache: dict[str, SourceUsageIndex] = {}

    def cache_stats(self) -> dict[str, int]:
        """Retained per-file cache sizes, for the memory checkpoint log."""
        usage_entries = sum(
            len(index.invocation_end_positions) + len(index.callable_ranges)
            for index in self._usage_index_cache.values()
        )
        return {
            "parsed_files": len(self._parsed_cache),
            "tree_nodes": self._parsed_nodes,
            "trees_evicted": self._trees_evicted,
            "line_files": len(self._file_content_cache),
            "lines": sum(len(lines) for lines in self._file_content_cache.values()),
            "usage_files": len(self._usage_index_cache),
            "usage_entries": usage_entries,
        }

    def get_source_line(self, file_path: Path, line: int) -> str | None:
        """Get a source line from cache, loading the file if needed."""
        lines = self.get_file_lines(file_path)
        if lines is None or line >= len(lines):
            return None
        return lines[line]

    def get_file_lines(self, file_path: Path) -> list[str] | None:
        """Get all lines of a file from cache, loading if needed."""
        file_key = str(file_path)
        if file_key not in self._file_content_cache:
            content = self._read_file_bytes(file_path)
            if content is None:
                return None
            self._file_content_cache[file_key] = content.decode(errors="replace").splitlines()
        return self._file_content_cache[file_key]

    def is_invocation(self, file_path: Path, ref_line: int, ref_end_char: int) -> bool:
        """Check whether a reference is the target of a call-like AST node."""
        usage_index = self._usage_index(file_path)
        if usage_index is None:
            return True
        return (ref_line, ref_end_char) in usage_index.invocation_end_positions

    def is_callable_usage(self, file_path: Path, ref_line: int, ref_start_char: int, ref_end_char: int) -> bool:
        """Check whether a variable/constant reference is used in a callable context."""
        usage_index = self._usage_index(file_path)
        if usage_index is None:
            return True
        return (ref_line, ref_start_char, ref_end_char) in usage_index.callable_ranges

    def is_reference_in_declaration_body(
        self,
        file_path: Path,
        declaration_line: int,
        declaration_start_char: int,
        ref_line: int,
        ref_start_char: int,
        ref_end_char: int,
        *,
        include_expression_body: bool = False,
    ) -> bool:
        """Check whether a reference is structurally inside a declaration body."""
        parsed = self._parse(file_path)
        if parsed is None:
            return False

        node = self._smallest_named_node_covering_range(
            parsed.tree.root_node,
            ref_line,
            ref_start_char,
            ref_end_char,
        )
        while node is not None:
            body_starts_in_declaration = node.start_point.row > declaration_line or (
                node.start_point.row == declaration_line and node.start_point.column >= declaration_start_char
            )
            if body_starts_in_declaration and node.type in _DECLARATION_BLOCK_NODE_TYPES:
                return True
            if body_starts_in_declaration and include_expression_body and node.type in _EXPRESSION_BODY_NODE_TYPES:
                return True
            node = node.parent
        return False

    def find_call_sites(self, file_path: Path) -> list[CallSite]:
        """Find definition-query positions for identifiers used at call sites."""
        parsed = self._parse(file_path)
        if parsed is None:
            return []

        sites: list[CallSite] = []
        seen: set[tuple[int, int]] = set()
        for node in self._walk(parsed.tree.root_node):
            target = self._call_target_node(node)
            if target is None:
                continue
            pos = (target.start_point.row, target.start_point.column)
            if pos in seen:
                continue
            seen.add(pos)
            sites.append(CallSite.from_lsp_position(file=str(file_path), line=pos[0], column=pos[1]))
        return sites

    def find_method_group_sites(self, file_path: Path) -> list[CallSite]:
        """Positions of arguments passed by name instead of invoked (``MapGet("/i", Handler)``).

        Why separate from ``find_call_sites``: a handler passed as a value has no
        invocation node, so the call-target walk cannot see it — but the same
        position shape is also every ordinary argument, so the caller has to
        discard whatever does not resolve to something callable.
        """
        parsed = self._parse(file_path)
        if parsed is None:
            return []

        sites: list[CallSite] = []
        seen: set[tuple[int, int]] = set()
        for node in self._walk(parsed.tree.root_node):
            if node.type not in _CALLABLE_USAGE_ANCESTORS or not self._parent_is_call_like(node):
                continue
            for child in node.named_children:
                # Only the argument itself: a named argument (``f(handler: H)``)
                # keeps its label as the first named child.
                expression = (
                    child.named_children[-1] if child.type in _ARGUMENT_NODE_TYPES and child.named_children else child
                )
                target = self._select_query_node(expression)
                if target is None:
                    continue
                pos = (target.start_point.row, target.start_point.column)
                if pos in seen:
                    continue
                seen.add(pos)
                sites.append(CallSite.from_lsp_position(file=str(file_path), line=pos[0], column=pos[1]))
        return sites

    @staticmethod
    def _read_file_bytes(file_path: Path) -> bytes | None:
        """Read a file's bytes. Deliberately uncached — both consumers (the
        parse tree and the decoded line list) cache their own derived product,
        so retaining the raw bytes as well just holds a third copy of the repo.
        """
        try:
            return file_path.read_bytes()
        except OSError:
            return None

    def _parse(self, file_path: Path) -> ParsedSource | None:
        file_key = str(file_path)
        cached = self._parsed_cache.get(file_key)
        if cached is not None:
            self._parsed_cache.move_to_end(file_key)
            return cached

        content = self._read_file_bytes(file_path)
        if content is None:
            return None
        parser = self._parser_for(file_path)
        if parser is None:
            return None

        parsed = ParsedSource(content=content, tree=parser.parse(content))
        self._parsed_cache[file_key] = parsed
        self._parsed_nodes += parsed.tree.root_node.descendant_count
        self._evict_trees()
        return parsed

    def _evict_trees(self) -> None:
        """Drop least-recently-parsed trees until the node budget is met."""
        while self._parsed_nodes > self._tree_node_budget and len(self._parsed_cache) > 1:
            _, evicted = self._parsed_cache.popitem(last=False)
            self._parsed_nodes -= evicted.tree.root_node.descendant_count
            self._trees_evicted += 1

    def _usage_index(self, file_path: Path) -> SourceUsageIndex | None:
        file_key = str(file_path)
        if file_key in self._usage_index_cache:
            return self._usage_index_cache[file_key]

        parsed = self._parse(file_path)
        if parsed is None:
            return None

        invocation_end_positions: set[tuple[int, int]] = set()
        callable_ranges: set[tuple[int, int, int]] = set()
        for node in self._walk(parsed.tree.root_node):
            target = self._call_target_node(node)
            if target is not None:
                invocation_end_positions.add((target.end_point.row, target.end_point.column))
                callable_ranges.add((target.start_point.row, target.start_point.column, target.end_point.column))
                continue

            if not node.is_named:
                continue
            if self._node_is_return_value(node) or self._node_is_call_argument(node):
                callable_ranges.add((node.start_point.row, node.start_point.column, node.end_point.column))

        usage_index = SourceUsageIndex(
            invocation_end_positions=invocation_end_positions,
            callable_ranges=callable_ranges,
        )
        self._usage_index_cache[file_key] = usage_index
        return usage_index

    def _parser_for(self, file_path: Path) -> Parser | None:
        suffix = file_path.suffix.lower()
        factory = _LANGUAGE_BY_SUFFIX.get(suffix)
        if factory is None:
            return None
        if suffix not in self._parser_by_suffix:
            parser = Parser()
            parser.language = TreeSitterLanguage(factory())
            self._parser_by_suffix[suffix] = parser
        return self._parser_by_suffix[suffix]

    def _call_target_node(self, node: TreeSitterNode) -> TreeSitterNode | None:
        if node.type in _CALL_NODE_TYPES:
            function = (
                node.child_by_field_name("function")
                or node.child_by_field_name("constructor")
                or node.child_by_field_name("name")
            )
            return self._select_query_node(function)
        if node.type in _CONSTRUCTOR_NODE_TYPES:
            for field_name in _CONSTRUCTOR_FIELD_NAMES:
                target = self._select_query_node(node.child_by_field_name(field_name))
                if target is not None:
                    return target
            return self._first_named_child_of_type(node, _NAME_NODE_TYPES)
        if node.type in _METHOD_REFERENCE_NODE_TYPES:
            return self._last_named_child_of_type(node, _NAME_NODE_TYPES)
        return None

    def _select_query_node(self, node: TreeSitterNode | None) -> TreeSitterNode | None:
        if node is None:
            return None
        for field_name in _CALL_TARGET_FIELD_NAMES:
            child = node.child_by_field_name(field_name)
            selected = self._select_query_node(child)
            if selected is not None:
                return selected
        if node.type in _GENERIC_TYPE_NODE_TYPES:
            return self._first_named_child_of_type(node, _NAME_NODE_TYPES)
        if node.type in _NAME_NODE_TYPES:
            return node
        return self._last_named_child_of_type(node, _NAME_NODE_TYPES)

    def _node_is_call_target(self, target: TreeSitterNode) -> bool:
        node = target
        while node.parent is not None:
            parent = node.parent
            if self._call_target_node(parent) == target:
                return True
            node = parent
        return False

    def find_type_bases(self, file_path: Path) -> list[tuple[str, list[str]]]:
        """Return ``(declared type name, base type names)`` for each type in the file.

        Why: csharp-ls answers neither ``textDocument/implementation`` nor
        ``typeHierarchy``, so the parse tree is the only place the inheritance
        needed to expand a virtual call into its overrides survives.
        """
        parsed = self._parse(file_path)
        if parsed is None:
            return []

        def text(node: TreeSitterNode) -> str:
            return parsed.content[node.start_byte : node.end_byte].decode("utf8", "replace")

        declarations: list[tuple[str, list[str]]] = []
        for node in self._walk(parsed.tree.root_node):
            if node.type not in _TYPE_DECLARATION_NODE_TYPES:
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            bases = [
                text(self._select_query_node(base) or base)
                for child in node.children
                if child.type in _BASE_LIST_NODE_TYPES
                for base in self._base_type_nodes(child)
            ]
            if bases:
                declarations.append((text(name_node), bases))
        return declarations

    @staticmethod
    def _base_type_nodes(base_list: TreeSitterNode) -> list[TreeSitterNode]:
        """The individual base types in a base list, past the wrappers grammars add."""
        nodes: list[TreeSitterNode] = []
        for base in base_list.named_children:
            if base.type in _BASE_GROUP_NODE_TYPES:
                nodes.extend(base.named_children)
                continue
            nodes.append(base.child_by_field_name("type") or base)
        return nodes

    def find_member_modifiers(self, file_path: Path) -> dict[tuple[str, str], frozenset[str]]:
        """C# modifiers on each ``(declaring type, member)`` the file declares.

        Why: whether a call can dispatch to a same-named member of a derived type
        is a modifier question — ``new``, ``static`` and plain redeclarations bind
        to the base — and no LSP request this engine makes carries modifiers.
        """
        parsed = self._parse(file_path)
        if parsed is None:
            return {}

        def text(node: TreeSitterNode) -> str:
            return parsed.content[node.start_byte : node.end_byte].decode("utf8", "replace")

        modifiers: dict[tuple[str, str], frozenset[str]] = {}
        for node in self._walk(parsed.tree.root_node):
            if node.type not in _TYPE_DECLARATION_NODE_TYPES:
                continue
            type_name_node = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if type_name_node is None or body is None:
                continue
            type_name = text(type_name_node)
            for member in body.named_children:
                if member.type not in _MEMBER_DECLARATION_NODE_TYPES:
                    continue
                member_name_node = member.child_by_field_name("name")
                if member_name_node is None:
                    continue
                found = {text(child) for child in member.children if child.type == "modifier"}
                if any(child.type == "explicit_interface_specifier" for child in member.children):
                    found.add("explicit")
                modifiers[(type_name, text(member_name_node))] = frozenset(found)
        return modifiers

    @staticmethod
    def _node_is_return_value(target: TreeSitterNode) -> bool:
        node = target
        while node.parent is not None:
            parent = node.parent
            if parent.type in {"return_statement", "return_statement2"}:
                return True
            if parent.type in _CALLABLE_USAGE_ANCESTORS:
                return False
            node = parent
        return False

    def _node_is_call_argument(self, target: TreeSitterNode) -> bool:
        node = target
        while node.parent is not None:
            parent = node.parent
            if parent.type in _CALLABLE_USAGE_ANCESTORS and self._parent_is_call_like(parent):
                return True
            if self._call_target_node(parent) == target:
                return False
            node = parent
        return False

    @staticmethod
    def _parent_is_call_like(node: TreeSitterNode) -> bool:
        parent = node.parent
        if parent is None:
            return False
        return parent.type in _CALL_NODE_TYPES or parent.type in _CONSTRUCTOR_NODE_TYPES

    def _smallest_named_node_ending_at(self, node: TreeSitterNode, line: int, column: int) -> TreeSitterNode | None:
        best: TreeSitterNode | None = None
        if not self._node_contains_point(node, line, column):
            return None
        candidates = [node]
        while candidates:
            candidate = candidates.pop()
            if candidate.is_named and candidate.end_point.row == line and candidate.end_point.column == column:
                if best is None or self._node_size(candidate) < self._node_size(best):
                    best = candidate
            candidates.extend(child for child in candidate.children if self._node_contains_point(child, line, column))
        return best

    def _smallest_named_node_covering_range(
        self, node: TreeSitterNode, line: int, start_column: int, end_column: int
    ) -> TreeSitterNode | None:
        best: TreeSitterNode | None = None
        if not self._node_covers_range(node, line, start_column, end_column):
            return None
        candidates = [node]
        while candidates:
            candidate = candidates.pop()
            if candidate.is_named:
                if best is None or self._node_size(candidate) < self._node_size(best):
                    best = candidate
            candidates.extend(
                child for child in candidate.children if self._node_covers_range(child, line, start_column, end_column)
            )
        return best

    @staticmethod
    def _node_contains_point(node: TreeSitterNode, line: int, column: int) -> bool:
        start = node.start_point
        end = node.end_point
        if start.row > line or end.row < line:
            return False
        if start.row == line and start.column > column:
            return False
        if end.row == line and end.column < column:
            return False
        return True

    @staticmethod
    def _node_covers_range(node: TreeSitterNode, line: int, start_column: int, end_column: int) -> bool:
        start = node.start_point
        end = node.end_point
        if start.row > line or end.row < line:
            return False
        if start.row == line and start.column > start_column:
            return False
        if end.row == line and end.column < end_column:
            return False
        return True

    @staticmethod
    def _node_size(node: TreeSitterNode) -> int:
        return node.end_byte - node.start_byte

    def _first_named_child_of_type(self, node: TreeSitterNode, node_types: frozenset[str]) -> TreeSitterNode | None:
        for child in self._walk(node):
            if child is not node and child.type in node_types:
                return child
        return None

    def _last_named_child_of_type(self, node: TreeSitterNode, node_types: frozenset[str]) -> TreeSitterNode | None:
        result: TreeSitterNode | None = None
        for child in self._walk(node):
            if child is not node and child.type in node_types:
                result = child
        return result

    def _walk(self, node: TreeSitterNode):
        yield node
        for child in node.children:
            yield from self._walk(child)
