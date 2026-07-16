"""Source code reading and tree-sitter call-site detection utilities."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Language as TreeSitterLanguage
from tree_sitter import Node as TreeSitterNode
from tree_sitter import Parser, Tree

from static_analyzer.constants import LANGUAGE_EXTENSIONS, Language
from static_analyzer.engine.models import CallSite, ImportDependency, ImportDependencyKind

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
_IMPORT_NODE_TYPES = frozenset(
    {
        "import_statement",
        "import_from_statement",
        "import_declaration",
        "import_spec",
        "export_statement",
        "namespace_use_declaration",
        "namespace_use_clause",
        "use_declaration",
        "using_directive",
        "extern_crate_declaration",
        "mod_item",
    }
)


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

    def __init__(self) -> None:
        self._file_content_cache: dict[str, list[str]] = {}
        self._file_bytes_cache: dict[str, bytes] = {}
        self._parsed_cache: dict[str, ParsedSource] = {}
        self._parser_by_suffix: dict[str, Parser] = {}
        self._usage_index_cache: dict[str, SourceUsageIndex] = {}

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

    def find_import_declarations(self, file_path: Path) -> list[ImportDependency]:
        """Extract static imports from tree-sitter import nodes.

        Resolution is intentionally left to CallGraphBuilder, which owns the
        symbol table and language adapter. Dynamic/computed imports are ignored.
        """
        parsed = self._parse(file_path)
        if parsed is None:
            return []

        imports: set[ImportDependency] = set()
        for node in self._walk(parsed.tree.root_node):
            if node.type not in _IMPORT_NODE_TYPES:
                continue
            if node.parent is not None and node.parent.type in _IMPORT_NODE_TYPES:
                continue
            text = parsed.content[node.start_byte : node.end_byte].decode(errors="replace")
            for module, offset in self._import_modules(text, file_path.suffix.lower()):
                prefix = text[:offset]
                line_offset = prefix.count("\n")
                if line_offset:
                    column = len(prefix.rsplit("\n", 1)[-1]) + 1
                else:
                    column = node.start_point.column + offset + 1
                imports.add(
                    ImportDependency(
                        source_file=str(file_path),
                        declared_module=module,
                        line=node.start_point.row + line_offset + 1,
                        column=column,
                        kind=(ImportDependencyKind.MODULE if node.type == "mod_item" else ImportDependencyKind.IMPORT),
                    )
                )
        return sorted(imports, key=lambda item: (item.source_file, item.line, item.column, item.declared_module))

    @staticmethod
    def _import_modules(text: str, suffix: str) -> list[tuple[str, int]]:
        if suffix == ".py":
            from_match = re.search(r"\bfrom\s+([.\w]+)\s+import\b", text)
            if from_match:
                return [(from_match.group(1), from_match.start(1))]
            import_match = re.search(r"\bimport\s+(.+)", text, flags=re.DOTALL)
            if not import_match:
                return []
            python_imports = []
            import_list = import_match.group(1)
            cursor = import_match.start(1)
            for item in import_list.split(","):
                name_match = re.search(r"[A-Za-z_]\w*(?:\.\w+)*", item)
                if name_match:
                    python_imports.append((name_match.group(0), cursor + name_match.start()))
                cursor += len(item) + 1
            return sorted(set(python_imports), key=lambda item: (item[1], item[0]))

        if suffix == ".php" and "\\{" in text:
            group_match = re.search(
                r"\buse\s+(?:function\s+|const\s+)?([\w\\]+)\\\{([^}]+)\}",
                text,
            )
            if group_match:
                prefix = group_match.group(1).rstrip("\\")
                php_imports = []
                group_items = group_match.group(2)
                cursor = group_match.start(2)
                for item in group_items.split(","):
                    name_match = re.search(r"[A-Za-z_]\w*", item)
                    if name_match:
                        php_imports.append((f"{prefix}\\{name_match.group(0)}", cursor + name_match.start()))
                    cursor += len(item) + 1
                return sorted(set(php_imports), key=lambda item: (item[1], item[0]))

        patterns: list[str]
        if suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}:
            patterns = [
                r"\bfrom\s*['\"]([^'\"]+)['\"]",
                r"\bimport\s*['\"]([^'\"]+)['\"]",
                r"\brequire\s*\(\s*['\"]([^'\"]+)['\"]",
            ]
        elif suffix == ".java":
            patterns = [r"\bimport\s+(?:static\s+)?([\w.*]+)"]
        elif suffix == ".go":
            patterns = [r"['\"]([^'\"]+)['\"]"]
        elif suffix == ".php":
            patterns = [
                r"\buse\s+(?:function\s+|const\s+)?([\w\\]+)",
                r"\b(?:include|include_once|require|require_once)\s*\(?\s*['\"]([^'\"]+)['\"]",
            ]
        elif suffix == ".rs":
            patterns = [r"\buse\s+([\w:]+)", r"\bextern\s+crate\s+([\w]+)", r"\bmod\s+([\w]+)"]
        elif suffix == ".cs":
            patterns = [r"\busing\s+(?:static\s+)?(?:\w+\s*=\s*)?([\w.]+)"]
        else:
            patterns = []

        found: dict[tuple[str, int], None] = {}
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                # A Rust grouped import leaves the separator before ``{`` in
                # this regex match; that separator is syntax, not identity.
                module = match.group(1).strip().rstrip(";,: ")
                if module:
                    found[(module, match.start(1))] = None
        return sorted(found, key=lambda item: (item[1], item[0]))

    def _read_file_bytes(self, file_path: Path) -> bytes | None:
        file_key = str(file_path)
        if file_key not in self._file_bytes_cache:
            try:
                self._file_bytes_cache[file_key] = file_path.read_bytes()
            except OSError:
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
