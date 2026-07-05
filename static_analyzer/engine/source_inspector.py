"""Source code reading and tree-sitter call-site detection utilities."""

from __future__ import annotations

import logging
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


@dataclass(frozen=True)
class ParsedSource:
    content: bytes
    tree: Tree


class SourceInspector:
    """Reads source files and finds call sites from tree-sitter ASTs."""

    def __init__(self) -> None:
        self._file_content_cache: dict[str, list[str]] = {}
        self._file_bytes_cache: dict[str, bytes] = {}
        self._parsed_cache: dict[str, ParsedSource] = {}
        self._parser_by_suffix: dict[str, Parser] = {}

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
        parsed = self._parse(file_path)
        if parsed is None:
            return True

        target = self._smallest_named_node_ending_at(parsed.tree.root_node, ref_line, ref_end_char)
        if target is None:
            return False
        return self._node_is_call_target(target)

    def is_callable_usage(self, file_path: Path, ref_line: int, ref_start_char: int, ref_end_char: int) -> bool:
        """Check whether a variable/constant reference is used in a callable context."""
        parsed = self._parse(file_path)
        if parsed is None:
            return True

        target = self._smallest_named_node_covering_range(parsed.tree.root_node, ref_line, ref_start_char, ref_end_char)
        if target is None:
            return False
        if self._node_is_call_target(target):
            return True
        if self._node_is_return_value(target):
            return True
        return self._node_is_call_argument(target)

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

    def _read_file_bytes(self, file_path: Path) -> bytes | None:
        file_key = str(file_path)
        if file_key not in self._file_bytes_cache:
            try:
                self._file_bytes_cache[file_key] = file_path.read_bytes()
            except Exception:
                return None
        return self._file_bytes_cache[file_key]

    def _parse(self, file_path: Path) -> ParsedSource | None:
        file_key = str(file_path)
        if file_key in self._parsed_cache:
            return self._parsed_cache[file_key]

        content = self._read_file_bytes(file_path)
        if content is None:
            return None
        parser = self._parser_for(file_path)
        if parser is None:
            return None

        parsed = ParsedSource(content=content, tree=parser.parse(content))
        self._parsed_cache[file_key] = parsed
        return parsed

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
        for candidate in self._walk(node):
            if not candidate.is_named:
                continue
            if candidate.end_point.row != line or candidate.end_point.column != column:
                continue
            if best is None or self._node_size(candidate) < self._node_size(best):
                best = candidate
        return best

    def _smallest_named_node_covering_range(
        self, node: TreeSitterNode, line: int, start_column: int, end_column: int
    ) -> TreeSitterNode | None:
        best: TreeSitterNode | None = None
        for candidate in self._walk(node):
            if not candidate.is_named:
                continue
            if candidate.start_point.row > line or candidate.end_point.row < line:
                continue
            if candidate.start_point.row == line and candidate.start_point.column > start_column:
                continue
            if candidate.end_point.row == line and candidate.end_point.column < end_column:
                continue
            if best is None or self._node_size(candidate) < self._node_size(best):
                best = candidate
        return best

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
