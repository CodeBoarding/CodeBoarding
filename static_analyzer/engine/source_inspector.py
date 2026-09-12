"""Source code reading and tree-sitter call-site detection utilities."""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Language as TreeSitterLanguage
from tree_sitter import Node as TreeSitterNode
from tree_sitter import Parser, Point, Tree

from static_analyzer.config import LANGUAGE_EXTENSIONS, Language, NodeType
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
        "nullsafe_member_call_expression",
        "scoped_call_expression",
        "method_invocation",
        "invocation_expression",
        "explicit_constructor_invocation",
    }
)
_CONSTRUCTOR_NODE_TYPES = frozenset({"object_creation_expression", "new_expression"})
# C# target-typed ``new(...)``: no type name at the call site.
_IMPLICIT_CONSTRUCTOR_NODE_TYPES = frozenset({"implicit_object_creation_expression"})
# C# ``: this(...)`` / ``: base(...)`` constructor delegation.
_CONSTRUCTOR_INITIALIZER_NODE_TYPES = frozenset({"constructor_initializer"})
# Applying an attribute runs its constructor.
_ATTRIBUTE_NODE_TYPES = frozenset({"attribute"})
# Braces holding ``Prop = value`` are an object initializer, not a collection one.
_OBJECT_INITIALIZER_NODE_TYPES = frozenset({"assignment_expression"})
# Loops whose ``right`` field is a value whose type gets enumerated.
_ITERATION_NODE_TYPES = frozenset({"foreach_statement", "for_each_statement", "enhanced_for_statement"})
_METHOD_REFERENCE_NODE_TYPES = frozenset({"method_reference"})
# Expanding a macro runs its body, and the module declaring it is a real dependency.
_MACRO_INVOCATION_NODE_TYPES = frozenset({"macro_invocation"})
# Rendering an element runs its component; the closing tag is the same element named twice.
_JSX_ELEMENT_NODE_TYPES = frozenset({"jsx_opening_element", "jsx_self_closing_element"})
# Applying a decorator calls it. ``@cache(...)`` is already a call node, so only a bare
# name is a site of its own.
_DECORATOR_NODE_TYPES = frozenset({"decorator"})
# Everything a grammar writes above a declaration to decorate it.
_DECORATION_NODE_TYPES = frozenset({"decorator", "annotation", "marker_annotation", "attribute_list"})
_DECORATOR_NAME_NODE_TYPES = frozenset({"identifier", "attribute", "member_expression"})
# Declarations that hold a value, and the values that are functions: ``const f = () => ...``
# is as callable a target as a declared function.
_FUNCTION_VALUE_HOLDER_NODE_TYPES = frozenset(
    {"variable_declarator", "public_field_definition", "field_definition", "pair", "assignment", "property_signature"}
)
_FUNCTION_LITERAL_NODE_TYPES = frozenset(
    {"arrow_function", "function_expression", "function", "generator_function", "lambda"}
)
# ``receiver.member(...)``: the object field holds the receiver in both spellings.
_MEMBER_ACCESS_NODE_TYPES = frozenset({"member_expression", "attribute"})
# Statements that bind a name declared in another file.
_IMPORT_NODE_TYPES = frozenset(
    {
        "import_statement",
        "import_from_statement",
        "import_declaration",
        "import_spec",
        "namespace_use_declaration",
        "use_declaration",
        "using_directive",
    }
)
# Fields that hold what a declaration runs rather than how it is declared. A position in one
# of them is inside the body, however few lines the declaration is written on.
_BODY_FIELD_NAMES = ("body", "value", "right")
# Nodes that run a constructor. Java's `super(...)`/`this(...)` is a call node rather than a
# creation one, and `Dog::new` is a method reference that has to be told from `Dog::speak`.
_CONSTRUCTION_NODE_TYPES = (
    _CONSTRUCTOR_NODE_TYPES
    | _IMPLICIT_CONSTRUCTOR_NODE_TYPES
    | _CONSTRUCTOR_INITIALIZER_NODE_TYPES
    | _ATTRIBUTE_NODE_TYPES
    | frozenset({"explicit_constructor_invocation"})
)
_CALLABLE_USAGE_ANCESTORS = frozenset({"argument_list", "arguments"})
_NAME_NODE_TYPES = frozenset(
    {
        "identifier",
        "name",
        "property_identifier",
        "private_property_identifier",  # ECMAScript ``#member``
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
# An argument that carries its parameter's name beside its value.
_LABELLED_ARGUMENT_NODE_TYPES = frozenset({"keyword_argument"})
# Node types that bind a name to a value, and the field the value sits in. Every grammar
# spells the statement differently, so a callback bound to a name is only found per
# spelling; C#'s declarator names no field at all, so there the value is the last child.
_VALUE_FIELD_BY_BINDING = {
    "assignment": "right",  # Python
    "assignment_expression": "right",  # C#, Java, JavaScript, TypeScript, PHP
    "assignment_statement": "right",  # Go
    "short_var_declaration": "right",  # Go
    "variable_declarator": "value",  # Java, JavaScript, TypeScript; C# names no field
    "let_declaration": "value",  # Rust
    "default_parameter": "value",  # Python
    "typed_default_parameter": "value",  # Python
    "field_definition": "value",  # JavaScript class field
    "public_field_definition": "value",  # TypeScript class field
    "var_spec": "value",  # Go
}
_VALUE_BODY_NODE_TYPES = frozenset({"return_statement", "arrow_expression_clause"})
_NAME_SHAPED_NODE_TYPES = frozenset(
    {
        "identifier",
        "member_access_expression",
        "generic_name",
        "qualified_name",
        "attribute",
        "member_expression",
        "scoped_identifier",
        "field_access",
        "selector_expression",
    }
)
# Literals that hold a group of values, each of which can be a name: a dispatch table is
# written as one of these and every callable in it is reached through it.
_VALUE_GROUP_NODE_TYPES = frozenset(
    {
        "dictionary",
        "list",
        "tuple",
        "set",
        "object",
        "array",
        "pair",
        "keyword_argument",
        "array_creation_expression",
        "expression_list",
    }
)
_TYPE_DECLARATION_NODE_TYPES = frozenset(
    {"class_declaration", "interface_declaration", "record_declaration", "struct_declaration"}
)
_BASE_LIST_NODE_TYPES = frozenset({"base_list", "superclass", "super_interfaces", "extends_interfaces"})
# C# declarations csharp-ls reports as document symbols, and the LSP kind it gives each.
_CSHARP_NAMESPACE_NODE_TYPES = frozenset({"namespace_declaration", "file_scoped_namespace_declaration"})
_CSHARP_SYMBOL_KINDS: dict[str, int] = {
    "class_declaration": NodeType.CLASS,
    "record_declaration": NodeType.CLASS,
    "delegate_declaration": NodeType.CLASS,
    "struct_declaration": NodeType.STRUCT,
    "record_struct_declaration": NodeType.STRUCT,
    "interface_declaration": NodeType.INTERFACE,
    "enum_declaration": NodeType.ENUM,
    "enum_member_declaration": NodeType.ENUM_MEMBER,
    "method_declaration": NodeType.METHOD,
    "destructor_declaration": NodeType.METHOD,
    "constructor_declaration": NodeType.CONSTRUCTOR,
    "property_declaration": NodeType.PROPERTY,
    "indexer_declaration": NodeType.PROPERTY,
    "field_declaration": NodeType.FIELD,
    "event_field_declaration": NodeType.EVENT,
    "event_declaration": NodeType.EVENT,
    "operator_declaration": NodeType.OPERATOR,
    "conversion_operator_declaration": NodeType.OPERATOR,
}
_CSHARP_PARAMETER_LIST_NODE_TYPES = frozenset({"parameter_list", "bracketed_parameter_list"})
_CSHARP_TYPE_SYMBOL_NODE_TYPES = frozenset(
    {
        "class_declaration",
        "record_declaration",
        "delegate_declaration",
        "struct_declaration",
        "record_struct_declaration",
        "interface_declaration",
        "enum_declaration",
    }
)
_CSHARP_CALLABLE_MEMBER_NODE_TYPES = frozenset(
    {
        "method_declaration",
        "destructor_declaration",
        "constructor_declaration",
        "indexer_declaration",
        "operator_declaration",
        "conversion_operator_declaration",
    }
)
_CSHARP_LITERAL_NODE_TYPES = frozenset(
    {
        "string_literal",
        "verbatim_string_literal",
        "raw_string_literal",
        "character_literal",
        "interpolated_string_expression",
    }
)
_CSHARP_UNNAMED_MEMBER_NODE_TYPES = frozenset(
    {"indexer_declaration", "operator_declaration", "conversion_operator_declaration"}
)
# Java groups several bases under one node; C# wraps a record's base in its
# primary-constructor call, whose ``type`` field is the base itself.
_BASE_GROUP_NODE_TYPES = frozenset({"type_list"})
_MEMBER_DECLARATION_NODE_TYPES = frozenset(
    {"method_declaration", "property_declaration", "indexer_declaration", "event_declaration"}
)
# Conditional-compilation lines, blanked (not removed) so byte offsets survive.
# Restricted to languages where ``#`` opens a directive rather than a comment.
_DIRECTIVE_LINE = re.compile(rb"(?m)^[ \t]*#[^\n]*")
# An identifier as every supported grammar spells one, PHP's ``$name`` included.
_IDENTIFIER = re.compile(r"(?![0-9])[\w$]+")
_PREPROCESSOR_SUFFIXES = frozenset({".cs"})
# Ceiling on retained tree-sitter nodes. Trees are by far the largest thing this
# class touches — retaining one per file cost 2.2GB on a 5k-file C# repo — and
# the common path needs each exactly once, to build that file's usage index,
# which is cached separately and outlives the tree. Measured: going from
# unbounded to 500k costs no wall-clock, because nothing re-reads a tree.
TREE_NODE_BUDGET = 500_000


def _error_node_count(tree: Tree) -> int:
    count = 0
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            count += 1
        stack.extend(node.children)
    return count


def _line_start_offsets(content: bytes) -> tuple[int, ...]:
    """The byte offset at which each line of *content* begins."""
    offsets = [0]
    end = content.find(b"\n")
    while end != -1:
        offsets.append(end + 1)
        end = content.find(b"\n", end + 1)
    return tuple(offsets)


@dataclass(frozen=True)
class ParsedSource:
    content: bytes
    tree: Tree
    # Where every line of ``content`` begins, kept only for a file that needs the
    # conversion below.
    line_starts: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        # Why: tree-sitter counts a column in bytes, LSP in UTF-16 code units. The two
        # agree for an all-ASCII file, which is almost every file, so the offsets exist
        # only where they do not.
        if not self.content.isascii():
            object.__setattr__(self, "line_starts", _line_start_offsets(self.content))

    def lsp_position(self, point: Point) -> tuple[int, int]:
        """The line and character an LSP request must carry to name *point*."""
        if not self.line_starts or point.row >= len(self.line_starts):
            return point.row, point.column
        start = self.line_starts[point.row]
        prefix = self.content[start : start + point.column]
        if prefix.isascii():
            return point.row, point.column
        return point.row, sum(2 if ord(char) > 0xFFFF else 1 for char in prefix.decode("utf8", "replace"))

    def byte_column(self, line: int, character: int) -> int:
        """The tree-sitter column that the LSP *character* of *line* falls at."""
        if not self.line_starts or line >= len(self.line_starts):
            return character
        end = self.line_starts[line + 1] if line + 1 < len(self.line_starts) else len(self.content)
        raw = self.content[self.line_starts[line] : end]
        if raw.isascii():
            return character
        column = units = 0
        for char in raw.decode("utf8", "replace"):
            if units >= character:
                break
            units += 2 if ord(char) > 0xFFFF else 1
            column += len(char.encode("utf8"))
        return column + max(0, character - units)


@dataclass(frozen=True)
class ReceiverMember:
    """A ``receiver.member(...)`` call whose receiver is a bare name."""

    line: int
    column: int
    member: str


@dataclass(frozen=True)
class SourceUsageIndex:
    construction_start_positions: set[tuple[int, int]]
    function_value_positions: set[tuple[int, int]]


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
            len(index.construction_start_positions) + len(index.function_value_positions)
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

    def declared_name_at(self, file_path: Path, line: int, character: int) -> str:
        """The identifier this file declares at a zero-based position, or empty when none is.

        Why an import binding is not one: the name written in ``import { target as t }`` is
        declared in the file it comes from, and a server answers at that binding as readily
        as at a declaration. Reading it as a name this file declares matches it to whatever
        the file happens to declare under the same name.
        """
        source = self.get_source_line(file_path, line)
        if source is None or not 0 <= character < len(source):
            return ""
        match = _IDENTIFIER.match(source, character)
        if match is None or self._inside_import(file_path, line, character):
            return ""
        return match.group(0)

    def is_construction_site(self, site: CallSite) -> bool:
        """Whether the call at *site* runs a constructor.

        Why not permissive when the file cannot be parsed, as ``is_invocation`` is: this
        gates *synthesising* constructor edges, so an unreadable file should add none rather
        than one for every class a caller merely mentions.
        """
        usage_index = self._usage_index(Path(site.file))
        if usage_index is None:
            return False
        return (site.line - 1, site.column - 1) in usage_index.construction_start_positions

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
            pos = parsed.lsp_position(target.start_point)
            if pos in seen:
                continue
            seen.add(pos)
            sites.append(CallSite.from_lsp_position(file=str(file_path), line=pos[0], column=pos[1]))
        return sites

    def find_collection_initializer_sites(self, file_path: Path) -> list[CallSite]:
        """Creations whose braces hold elements rather than property assignments.

        ``new Bag { 1, 2 }`` calls ``Bag.Add`` once per element; the position is
        the creation's own, so the caller can hang the extra edges off whatever
        that resolves to.
        """
        parsed = self._parse(file_path)
        if parsed is None:
            return []

        sites: list[CallSite] = []
        seen: set[tuple[int, int]] = set()
        for node in self._walk(parsed.tree.root_node):
            if node.type not in _CONSTRUCTOR_NODE_TYPES | _IMPLICIT_CONSTRUCTOR_NODE_TYPES:
                continue
            initializer = node.child_by_field_name("initializer")
            if initializer is None or not any(
                child.type not in _OBJECT_INITIALIZER_NODE_TYPES for child in initializer.named_children
            ):
                continue
            target = self._call_target_node(node)
            if target is None:
                continue
            position = parsed.lsp_position(target.start_point)
            if position in seen:
                continue
            seen.add(position)
            sites.append(CallSite.from_lsp_position(file=str(file_path), line=position[0], column=position[1]))
        return sites

    def find_iterated_expression_sites(self, file_path: Path) -> list[CallSite]:
        """Positions of expressions being iterated over.

        Iterating a value calls ``GetEnumerator`` on its type, but the type is
        nowhere in the syntax — only the value is — so these positions are meant
        for a type query rather than the definition query the other sites use.
        """
        parsed = self._parse(file_path)
        if parsed is None:
            return []

        sites: list[CallSite] = []
        seen: set[tuple[int, int]] = set()
        for node in self._walk(parsed.tree.root_node):
            if node.type not in _ITERATION_NODE_TYPES:
                continue
            iterated = self._select_query_node(node.child_by_field_name("right"))
            if iterated is None:
                continue
            position = parsed.lsp_position(iterated.start_point)
            if position in seen:
                continue
            seen.add(position)
            sites.append(CallSite.from_lsp_position(file=str(file_path), line=position[0], column=position[1]))
        return sites

    def declares_function_value(self, file_path: Path, line: int, character: int) -> bool:
        """Whether the declaration at this position is a name bound to a function literal.

        Why: ``const handler = () => ...`` is a callable target a server reports as a
        variable, so a method group resolving to it would otherwise be discarded as a value.
        """
        usage_index = self._usage_index(file_path)
        if usage_index is None:
            return False
        return (line, character) in usage_index.function_value_positions

    def attribution_position(self, file_path: Path, line: int, character: int) -> tuple[int, int]:
        """The position that decides which declaration a call written here belongs to.

        Why: a decorator, annotation or attribute runs where it is written but belongs to the
        member below it, so the lookup is redirected to that member's own name. The gap
        between the two is not a measure of anything -- a call at module level can sit in it
        and belongs to nobody. Any other position speaks for itself.
        """
        parsed = self._parse(file_path)
        if parsed is None:
            return (line, character)
        column = parsed.byte_column(line, character)
        node = self._smallest_named_node_covering_range(parsed.tree.root_node, line, column, column)
        while node is not None and node.type not in _DECORATION_NODE_TYPES:
            node = node.parent
        while node is not None and node.parent is not None:
            declared = node.parent.child_by_field_name("name") or self._following_declaration_name(node)
            if declared is not None:
                return parsed.lsp_position(declared.start_point)
            node = node.parent
        return (line, character)

    def in_declaration_body(self, file_path: Path, declaration: tuple[int, int], line: int, character: int) -> bool:
        """Whether the position sits in the body of the declaration named at *declaration*.

        Why: a one-line ``function outer() { const cb = () => 1; }`` puts its body on its own
        declaration line, so "the declaration starts on this line" cannot tell a signature
        position from a body one, and a compact declaration would accept matches a spread one
        rejects. The declaration is identified by its name's position, which is what both
        symbol tables and graph nodes are keyed on.
        """
        parsed = self._parse(file_path)
        if parsed is None:
            return False
        column = parsed.byte_column(line, character)
        node = self._smallest_named_node_covering_range(parsed.tree.root_node, line, column, column)
        while node is not None:
            parent = node.parent
            # ``==``, not ``is``: tree-sitter hands back a fresh wrapper for the same node.
            if parent is not None and any(parent.child_by_field_name(field) == node for field in _BODY_FIELD_NAMES):
                name = parent.child_by_field_name("name")
                if name is not None and parsed.lsp_position(name.start_point) == declaration:
                    return True
            node = parent
        return False

    def receiver_member_calls(self, file_path: Path) -> dict[tuple[int, int], ReceiverMember]:
        """``receiver.member(...)`` sites with a bare-name receiver, by the member's position.

        Why: when the member's own definition leaves the repository -- an object typed as
        ``console``, a re-export -- the receiver still names something this repository
        declares, and the member it holds is the thing the call runs.
        """
        parsed = self._parse(file_path)
        if parsed is None:
            return {}

        calls: dict[tuple[int, int], ReceiverMember] = {}
        for node in self._walk(parsed.tree.root_node):
            if node.type not in _CALL_NODE_TYPES:
                continue
            access = node.child_by_field_name("function") or node.child_by_field_name("name")
            if access is None or access.type not in _MEMBER_ACCESS_NODE_TYPES:
                continue
            receiver = access.child_by_field_name("object")
            member = self._select_query_node(access)
            if receiver is None or receiver.type != "identifier" or member is None:
                continue
            receiver_line, receiver_column = parsed.lsp_position(receiver.start_point)
            calls.setdefault(
                parsed.lsp_position(member.start_point),
                ReceiverMember(
                    line=receiver_line,
                    column=receiver_column,
                    member=parsed.content[member.start_byte : member.end_byte].decode("utf8", "replace"),
                ),
            )
        return calls

    def find_method_group_sites(self, file_path: Path) -> list[CallSite]:
        """Positions where naming a method passes it as a value rather than calling it.

        Covers arguments (``MapGet("/i", Handler)``), event subscription
        (``consumer.Received += OnMessage``), assignment, ``return`` and
        expression bodies. Kept separate from ``find_call_sites`` because the
        same shapes are also every ordinary argument and every ordinary
        assignment, so the caller has to discard whatever does not resolve to
        something callable.
        """
        parsed = self._parse(file_path)
        if parsed is None:
            return []

        sites: list[CallSite] = []
        seen: set[tuple[int, int]] = set()
        for node in self._walk(parsed.tree.root_node):
            for expression in self._method_group_candidates(node):
                target = self._select_query_node(expression)
                if target is None:
                    continue
                pos = parsed.lsp_position(target.start_point)
                if pos in seen:
                    continue
                seen.add(pos)
                sites.append(CallSite.from_lsp_position(file=str(file_path), line=pos[0], column=pos[1]))
        return sites

    def _inside_import(self, file_path: Path, line: int, character: int) -> bool:
        parsed = self._parse(file_path)
        if parsed is None:
            return False
        column = parsed.byte_column(line, character)
        node = self._smallest_named_node_covering_range(parsed.tree.root_node, line, column, column)
        while node is not None:
            if node.type in _IMPORT_NODE_TYPES:
                return True
            node = node.parent
        return False

    def _method_group_candidates(self, node: TreeSitterNode) -> list[TreeSitterNode]:
        """Names in a position where naming something callable passes it as a value."""
        if node.type in _CALLABLE_USAGE_ANCESTORS and self._parent_is_call_like(node):
            return [name for child in node.named_children for name in self._value_names(child)]

        if node.type == "jsx_expression" and node.parent is not None and node.parent.type == "jsx_attribute":
            # ``onClick={handler}`` passes the handler exactly as an argument would.
            return [name for child in node.named_children for name in self._value_names(child)]

        field = _VALUE_FIELD_BY_BINDING.get(node.type)
        if field is not None:
            candidate = node.child_by_field_name(field)
            if candidate is None and len(node.named_children) > 1:
                candidate = node.named_children[-1]
        elif node.type in _VALUE_BODY_NODE_TYPES:
            candidate = node.named_children[0] if node.named_children else None
        else:
            return []
        return self._value_names(candidate) if candidate is not None else []

    def _value_names(self, value: TreeSitterNode) -> list[TreeSitterNode]:
        """The names this value is, or holds, that could denote something callable.

        A bare name is one; a group of values (a dispatch table, a list of handlers, a labelled
        argument) is each of the names inside it. Anything else -- an arithmetic expression, a
        call, a string -- is a value in its own right and names nothing that is being passed.
        """
        if value.type in _NAME_SHAPED_NODE_TYPES:
            return [value]
        if value.type in _VALUE_GROUP_NODE_TYPES:
            if value.type in _LABELLED_ARGUMENT_NODE_TYPES:
                held = [self._argument_value(value)]
            else:
                held = list(value.named_children)
            return [name for child in held for name in self._value_names(child)]
        if value.type in _ARGUMENT_NODE_TYPES and value.named_children:
            return self._value_names(value.named_children[-1])
        return []

    @staticmethod
    def _following_declaration_name(node: TreeSitterNode) -> TreeSitterNode | None:
        """The name of the first sibling declared after *node*.

        Why: some grammars keep a decoration beside what it decorates rather than inside it.
        """
        parent = node.parent
        if parent is None:
            return None
        for sibling in parent.named_children:
            if sibling.start_byte >= node.end_byte:
                name = sibling.child_by_field_name("name")
                if name is not None:
                    return name
        return None

    @staticmethod
    def _argument_value(argument: TreeSitterNode) -> TreeSitterNode:
        """A labelled argument carries its value beside the label; anything else is the value."""
        if argument.type in _LABELLED_ARGUMENT_NODE_TYPES:
            return argument.child_by_field_name("value") or argument
        if argument.type in _ARGUMENT_NODE_TYPES and argument.named_children:
            return argument.named_children[-1]
        return argument

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

        parsed = ParsedSource(content=content, tree=self._parse_tree(parser, content, file_path.suffix.lower()))
        self._parsed_cache[file_key] = parsed
        self._parsed_nodes += parsed.tree.root_node.descendant_count
        self._evict_trees()
        return parsed

    @staticmethod
    def _parse_tree(parser: Parser, content: bytes, suffix: str) -> Tree:
        """Parse *content*, retrying with conditional-compilation lines blanked.

        Why: a member body split across a ``#if`` — C#'s default-interface
        idiom, ``void Error<T>(...)`` then ``#if X`` then ``=> Write(...)`` —
        does not parse, and the call comes out as a constructor declaration
        inside an ERROR subtree, so the call-site walk cannot see it. Serilog's
        ILogger alone hides 55 calls that way. Blanking the directive lines
        keeps every other byte at its original offset, so positions still line
        up with what the language server was told about.
        """
        tree = parser.parse(content)
        if not tree.root_node.has_error or suffix not in _PREPROCESSOR_SUFFIXES:
            return tree
        blanked = _DIRECTIVE_LINE.sub(lambda m: b" " * len(m.group(0)), content)
        if blanked == content:
            return tree
        retried = parser.parse(blanked)
        # Fewer errors wins rather than zero errors: blanking leaves both arms of
        # an ``#if``/``#else`` in place, so a file can improve enormously and
        # still not parse cleanly. Serilog's ILogger goes from 310 error nodes
        # and 18 invocations to 2 and 96.
        return retried if _error_node_count(retried) < _error_node_count(tree) else tree

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

        construction_start_positions: set[tuple[int, int]] = set()
        function_value_positions: set[tuple[int, int]] = set()
        for node in self._walk(parsed.tree.root_node):
            declared = self._function_value_name(node)
            if declared is not None:
                function_value_positions.add(parsed.lsp_position(declared.start_point))

            target = self._call_target_node(node)
            if target is not None and self._runs_a_constructor(node):
                construction_start_positions.add(parsed.lsp_position(target.start_point))

        usage_index = SourceUsageIndex(
            construction_start_positions=construction_start_positions,
            function_value_positions=function_value_positions,
        )
        self._usage_index_cache[file_key] = usage_index
        return usage_index

    @staticmethod
    def _function_value_name(node: TreeSitterNode) -> TreeSitterNode | None:
        """The name a declaration binds, when what it binds is a function literal."""
        if node.type not in _FUNCTION_VALUE_HOLDER_NODE_TYPES:
            return None
        value = node.child_by_field_name("value") or node.child_by_field_name("right")
        if value is None or value.type not in _FUNCTION_LITERAL_NODE_TYPES:
            return None
        name = node.child_by_field_name("name") or node.child_by_field_name("key") or node.child_by_field_name("left")
        return name if name is not None and name.type in _NAME_NODE_TYPES else None

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

    def _runs_a_constructor(self, node: TreeSitterNode) -> bool:
        """Whether this call node constructs, rather than merely naming, a type."""
        if node.type in _CONSTRUCTION_NODE_TYPES:
            return True
        # `Dog::new` constructs; `Dog::speak` does not.
        if node.type in _METHOD_REFERENCE_NODE_TYPES:
            return any(child.type == "new" for child in node.children)
        return False

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
        if node.type in _ATTRIBUTE_NODE_TYPES:
            # Applying an attribute constructs it, and the annotated member
            # genuinely depends on that type.
            return self._select_query_node(node.child_by_field_name("name"))
        if node.type in _CONSTRUCTOR_INITIALIZER_NODE_TYPES:
            # ``: this(...)`` / ``: base(...)`` — the keyword is what the server
            # resolves to the delegated constructor.
            return next((child for child in node.children if child.type in ("this", "base")), None)
        if node.type in _IMPLICIT_CONSTRUCTOR_NODE_TYPES:
            # Target-typed ``new(...)``: the type lives on the assignment target,
            # so the only thing to query is the keyword itself. The server knows
            # what it infers to and answers with the constructor.
            return next((child for child in node.children if child.type == "new"), None)
        if node.type in _METHOD_REFERENCE_NODE_TYPES:
            return self._last_named_child_of_type(node, _NAME_NODE_TYPES)
        if node.type in _MACRO_INVOCATION_NODE_TYPES:
            return self._select_query_node(node.child_by_field_name("macro"))
        if node.type in _JSX_ELEMENT_NODE_TYPES:
            return self._select_query_node(node.child_by_field_name("name"))
        if node.type in _DECORATOR_NODE_TYPES:
            named = node.named_children
            if named and named[0].type in _DECORATOR_NAME_NODE_TYPES:
                return self._select_query_node(named[0])
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

    def find_document_symbols(self, file_path: Path) -> list[dict]:
        """C# document symbols read from the parse tree, shaped like csharp-ls reports them.

        Why: csharp-ls answers nothing for a file that belongs to more than one project
        (a shared source file linked into several), so calls into it resolve to a
        position no symbol covers. Names follow the server's display form --
        ``Add<T>(this IList<T> items, int count = 0)`` -- so the two sources of a
        symbol table agree on how a member is spelled.
        """
        if file_path.suffix.lower() != ".cs":
            return []
        parsed = self._parse(file_path)
        if parsed is None:
            return []

        def text(node: TreeSitterNode) -> str:
            raw = parsed.content[node.start_byte : node.end_byte].decode("utf8", "replace")
            # Whitespace inside a literal is the literal; between tokens it is layout.
            return raw if node.type in _CSHARP_LITERAL_NODE_TYPES else " ".join(raw.split())

        def point(at: Point) -> dict[str, int]:
            line, character = parsed.lsp_position(at)
            return {"line": line, "character": character}

        def symbol(node: TreeSitterNode, name: str, kind: int, selection: TreeSitterNode, children: list[dict]) -> dict:
            return {
                "name": name,
                "kind": int(kind),
                "range": {"start": point(node.start_point), "end": point(node.end_point)},
                "selectionRange": {"start": point(selection.start_point), "end": point(selection.end_point)},
                "children": children,
            }

        def parameters(node: TreeSitterNode) -> str:
            rendered: list[str] = []
            # The grammar leaves a ``params`` parameter as loose tokens between the commas.
            loose: list[str] = []
            for child in node.children:
                if child.type in ("(", ")", "[", "]", ","):
                    if loose:
                        rendered.append(" ".join(loose))
                        loose = []
                    continue
                if child.type != "parameter":
                    loose.append(text(child))
                    continue
                parts = [
                    text(part) for part in child.children if part.type not in ("attribute_list", "equals_value_clause")
                ]
                default = next((part for part in child.children if part.type == "equals_value_clause"), None)
                if default is not None:
                    parts.extend(["=", *(text(value) for value in default.named_children[:1])])
                rendered.append(" ".join(parts))
            if loose:
                rendered.append(" ".join(loose))
            # The server prints types without their ``pb::`` alias qualifiers.
            return re.sub(r"\b\w+::", "", ", ".join(rendered))

        def type_parameters(node: TreeSitterNode) -> str:
            params = node.child_by_field_name("type_parameters")
            if params is None:
                return ""
            names = [text(name) for param in params.named_children if (name := param.child_by_field_name("name"))]
            return f"<{', '.join(names)}>"

        def member_name(node: TreeSitterNode, name_node: TreeSitterNode | None) -> str:
            base = text(name_node) if name_node is not None else ""
            # ``void IFoo.M()`` and ``void IBar.M()`` are two members, as the server names them.
            explicit = next((child for child in node.children if child.type == "explicit_interface_specifier"), None)
            if explicit is not None:
                base = text(explicit) + base
            if node.type in ("method_declaration", "delegate_declaration"):
                base += type_parameters(node)
            params = next((child for child in node.children if child.type in _CSHARP_PARAMETER_LIST_NODE_TYPES), None)
            if node.type == "indexer_declaration":
                return f"{text(explicit) if explicit is not None else ''}this[{parameters(params) if params else ''}]"
            if node.type in ("operator_declaration", "conversion_operator_declaration"):
                # Everything between the ``operator`` keyword and the parameters names the
                # operator: ``+``, ``checked +``, or a conversion's target type.
                tokens = [child for child in node.children if child.type not in ("modifier", "attribute_list")]
                keyword = next((i for i, child in enumerate(tokens) if child.type == "operator"), len(tokens))
                after = tokens[keyword + 1 :]
                until = next((i for i, token in enumerate(after) if token.type in _CSHARP_PARAMETER_LIST_NODE_TYPES), 0)
                operator = " ".join(text(token) for token in after[:until])
                return f"operator {operator}({parameters(params) if params is not None else ''})"
            if node.type == "destructor_declaration":
                base = "~" + base
            if params is not None and node.type != "record_declaration":
                base += f"({parameters(params)})"
            return base

        def declarations(nodes: list[TreeSitterNode], enclosing: str = "") -> list[dict]:
            found: list[dict] = []
            for index, node in enumerate(nodes):
                if node.type in _CSHARP_NAMESPACE_NODE_TYPES:
                    name_node = node.child_by_field_name("name")
                    if name_node is None:
                        continue
                    body = node.child_by_field_name("body")
                    # A file-scoped namespace has no body: everything after it is inside it.
                    members = body.named_children if body is not None else nodes[index + 1 :]
                    # The server names a namespace in full, ``A.B`` for ``namespace A { namespace B``.
                    full_name = f"{enclosing}.{text(name_node)}" if enclosing else text(name_node)
                    namespace = symbol(node, full_name, NodeType.NAMESPACE, name_node, declarations(members, full_name))
                    namespace["detail"] = full_name
                    found.append(namespace)
                    if body is None:
                        break
                    continue
                kind = _CSHARP_SYMBOL_KINDS.get(node.type)
                if kind is None:
                    continue
                if node.type in ("field_declaration", "event_field_declaration"):
                    declaration = next(
                        (child for child in node.named_children if child.type == "variable_declaration"), None
                    )
                    for declarator in declaration.named_children if declaration is not None else []:
                        name_node = declarator.child_by_field_name("name")
                        if declarator.type == "variable_declarator" and name_node is not None:
                            found.append(symbol(node, text(name_node), kind, name_node, []))
                    continue
                name_node = node.child_by_field_name("name")
                if node.type in _CSHARP_CALLABLE_MEMBER_NODE_TYPES:
                    if name_node is None and node.type not in _CSHARP_UNNAMED_MEMBER_NODE_TYPES:
                        continue
                    found.append(symbol(node, member_name(node, name_node), kind, name_node or node, []))
                    continue
                if name_node is None:
                    continue
                children: list[dict] = []
                name = text(name_node)
                if node.type in _CSHARP_TYPE_SYMBOL_NODE_TYPES:
                    name += type_parameters(node)
                    body = node.child_by_field_name("body")
                    children = declarations(body.named_children) if body is not None else []
                found.append(symbol(node, name, kind, name_node, children))
            return found

        root = parsed.tree.root_node
        file_symbol = symbol(root, file_path.name, NodeType.FILE, root, declarations(root.named_children))
        return [file_symbol] if file_symbol["children"] else []

    @staticmethod
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
            if candidate.is_named and (best is None or self._node_size(candidate) < self._node_size(best)):
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
