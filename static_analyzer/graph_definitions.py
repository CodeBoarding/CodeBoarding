"""Resolve a definition position against a merged call graph, expanded the way the engine expands it.

Shared by the warm-start updater (definitions of changed files, against the cached
graph) and the cross-engine linker (definitions one engine could not name, against
every engine's graph), so the two cannot disagree on which nodes a call site reaches.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from static_analyzer.cfg import CallGraph, EdgeKind
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_constants import DISPATCHED_KINDS
from static_analyzer.engine.models import CallSite
from static_analyzer.engine.protocols import EdgeBuildAdapter
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.utils import definition_location
from static_analyzer.internal_references import parent_qualified_name, simple_name
from static_analyzer.node import Node

# How a call site was found in source; it decides what a resolved definition may become.
CALL = "call"
METHOD_GROUP = "method_group"
MEMBER_READ = "member_read"
MEMBER_WRITE = "member_write"
COLLECTION_INITIALIZER = "collection"
ITERATED = "iterated"


@dataclass(frozen=True)
class CallShapes:
    """Which shape each call site in one file has, so the full build and every update resolve it alike."""

    call_sites: list[CallSite]
    method_group: set[tuple[int, int]]
    member_read: set[tuple[int, int]]
    member_write: set[tuple[int, int]]
    collection: set[tuple[int, int]]
    iterated: set[tuple[int, int]]

    def definition_kind_at(self, position: tuple[int, int]) -> str:
        """Which shape a definition query at this position resolves as."""
        if position in self.method_group:
            return METHOD_GROUP
        if position in self.member_read:
            return MEMBER_READ
        if position in self.member_write:
            return MEMBER_WRITE
        if position in self.collection:
            return COLLECTION_INITIALIZER
        return CALL

    def requests_at(self, position: tuple[int, int]) -> list[tuple[str, str]]:
        """The ``(LSP method, call-site kind)`` pairs a cached site here is owed.

        Why more than one: a position can be two shapes at once -- ``foreach (var x in
        GetItems())`` is a call and an iteration -- and the full build runs both passes
        over it, so restoring the cached edge cannot ask only one of them.
        """
        if position not in self.iterated:
            return [("definition", self.definition_kind_at(position))]
        iterating = ("type_definition", ITERATED)
        if not any((site.lsp_line, site.lsp_column) == position for site in self.call_sites):
            return [iterating]
        return [("definition", self.definition_kind_at(position)), iterating]


def call_shapes(
    file_path: Path, inspector: SourceInspector, adapter: EdgeBuildAdapter, callable_names: Collection[str]
) -> CallShapes:
    """Every call site the file writes, and the shape of each, by the adapter's capabilities.

    *callable_names* are the names the graph declares callables under: a member named anything
    else cannot reach one, so it is not worth a query.
    """
    call_sites = inspector.find_call_sites(file_path)
    claimed = {(site.lsp_line, site.lsp_column) for site in call_sites}
    method_group: set[tuple[int, int]] = set()
    member_read: set[tuple[int, int]] = set()
    member_write: set[tuple[int, int]] = set()
    if adapter.resolves_method_groups:
        reads, writes = inspector.find_member_sites(file_path, callable_names)
        for shape, found in (
            (method_group, inspector.find_method_group_sites(file_path)),
            (member_read, reads),
            (member_write, writes),
        ):
            for site in found:
                position = (site.lsp_line, site.lsp_column)
                if position in claimed:
                    continue
                claimed.add(position)
                shape.add(position)
                call_sites.append(site)
    collection: set[tuple[int, int]] = set()
    if adapter.resolves_collection_initializers:
        collection = {
            (site.lsp_line, site.lsp_column) for site in inspector.find_collection_initializer_sites(file_path)
        }
    iterated: set[tuple[int, int]] = set()
    if adapter.resolves_iterated_types:
        iterated = {(site.lsp_line, site.lsp_column) for site in inspector.find_iterated_expression_sites(file_path)}
    return CallShapes(call_sites, method_group, member_read, member_write, collection, iterated)


@dataclass(frozen=True)
class CallTargets:
    """What a call site reaches: the graph's nodes, and the declarations still owed an implementation query."""

    nodes: list[Node]
    implementations: list[tuple[str, int, int]]

    @classmethod
    def none(cls) -> "CallTargets":
        return cls([], [])


class GraphIndex:
    """A graph's nodes by file, so a position lookup does not scan every node."""

    def __init__(self, call_graph: CallGraph, inspector: SourceInspector) -> None:
        self.call_graph = call_graph
        self.inspector = inspector
        self._by_file: dict[str, list[Node]] = defaultdict(list)
        for node in call_graph.nodes.values():
            self._by_file[node.file_path].append(node)
        self._by_name: dict[str, dict[str, dict[tuple[int, int], Node]]] = {}

    def nodes_in(self, file_path: str) -> list[Node]:
        return self._by_file.get(file_path, [])

    @cached_property
    def callable_names(self) -> set[str]:
        """The names the graph declares callables under."""
        return {simple_name(node.fully_qualified_name) for node in self.call_graph.nodes.values() if node.is_callable()}

    def declaration_at(self, file_path: str, line: int, character: int) -> Node | None:
        """The node a definition result names, by the engine's matching rules.

        Exact position; else the declaration at the function literal a name there is bound to; else
        the sole callable or class the file declares under the name declared there -- an overload
        signature, which the graph does not hold. Anything else matches nothing.
        """
        if not self.nodes_in(file_path):
            return None
        exact = self._declared_at(file_path, line, character)
        if exact is None:
            literal = self.inspector.function_values(Path(file_path)).get((line, character))
            exact = self._declared_at(file_path, *literal) if literal is not None else None
        return exact if exact is not None else self._sole_declaration_named(file_path, line, character)

    def _declared_at(self, file_path: str, line: int, character: int) -> Node | None:
        return _innermost(
            node for node in self.nodes_in(file_path) if (node.line_start, node.col_start) == (line + 1, character)
        )

    def _sole_declaration_named(self, file_path: str, line: int, character: int) -> Node | None:
        name = self.inspector.declared_name_at(Path(file_path), line, character)
        if not name:
            return None
        declared = self._declarations_by_name(file_path).get(name)
        if declared is None or len(declared) != 1:
            return None
        return next(iter(declared.values()))

    def _declarations_by_name(self, file_path: str) -> dict[str, dict[tuple[int, int], Node]]:
        """Callables and classes the file declares, by name then by position.

        Why keyed by position: one declaration registered under two qualified names is one
        declaration, and must not read as an ambiguous name.
        """
        cached = self._by_name.get(file_path)
        if cached is not None:
            return cached
        by_name: dict[str, dict[tuple[int, int], Node]] = {}
        for node in self.nodes_in(file_path):
            if not (node.is_callable() or node.is_class()):
                continue
            at = by_name.setdefault(simple_name(node.fully_qualified_name), {})
            position = (node.line_start, node.col_start)
            held = at.get(position)
            if held is None or len(node.fully_qualified_name) > len(held.fully_qualified_name):
                at[position] = node
        self._by_name[file_path] = by_name
        return by_name


def position_inside_node(node: Node, zero_based_line: int, character: int) -> bool:
    line = zero_based_line + 1
    if line < node.line_start or line > node.line_end:
        return False
    if line == node.line_start and character < node.col_start:
        return False
    return True


def most_specific_node_at_position(
    index: GraphIndex, file_path: str, line: int, char: int, callable_only: bool = False
) -> Node | None:
    """The innermost node containing the position -- which declaration a call site sits in."""
    return _innermost(
        node
        for node in index.nodes_in(file_path)
        if (not callable_only or node.is_callable()) and position_inside_node(node, line, char)
    )


def _innermost(nodes: Iterable[Node]) -> Node | None:
    """The last-starting, shortest-spanning, most specific of the candidates."""
    matches = list(nodes)
    if not matches:
        return None
    return max(
        matches,
        key=lambda node: (node.line_start, node.col_start, -node.line_end, len(node.fully_qualified_name)),
    )


def containing_source_node(index: GraphIndex, file_path: str, line: int, char: int) -> Node | None:
    """The node an edge from this position belongs to, callable or otherwise.

    A field initializer -- ``private readonly Store _store = new Store();`` -- calls
    a constructor from outside any method, so a callable-only lookup finds nothing
    and the edge is dropped. The full rebuild attributes it to the enclosing class,
    and every later pass has to agree or it loses the edge on every edit to that file.
    A decoration is redirected to the member it decorates for the same reason: the
    engine's ``_caller_at`` does it, so a warm start that did not would credit a
    class-member decorator to its class and a module-level one to nobody.
    """
    line, char = index.inspector.attribution_position(Path(file_path), line, char)
    callable_node = most_specific_node_at_position(index, file_path, line, char, callable_only=True)
    if callable_node is not None:
        return callable_node
    return most_specific_node_at_position(index, file_path, line, char)


def nodes_at_location(
    index: GraphIndex, file_path: str, line: int, character: int, include_callable_parent: bool = False
) -> list[Node]:
    """The node declared at an LSP position, plus the class a callable or constructor belongs to."""
    target = index.declaration_at(file_path, line, character)
    if target is None:
        return []
    return _with_owning_class(index, target, include_owner=include_callable_parent)


def _with_owning_class(index: GraphIndex, target: Node, include_owner: bool) -> list[Node]:
    """The declaration, and the class it belongs to when a call on it reaches that too."""
    if not ((include_owner and target.is_callable()) or target.type.name == "CONSTRUCTOR"):
        return [target]
    owner = index.call_graph.nodes.get(parent_qualified_name(target.fully_qualified_name))
    return [target, owner] if owner is not None and owner.is_class() else [target]


def definition_nodes(index: GraphIndex, definition: dict, include_callable_parent: bool = False) -> list[Node]:
    location = definition_location(definition)
    if location is None:
        return []
    file_path, line, character = location
    return nodes_at_location(index, str(file_path), line, character, include_callable_parent)


def members_named(call_graph: CallGraph, owner: Node, name: str) -> list[Node]:
    """Nodes for ``owner``'s members called *name*, by qualified-name prefix.

    The full rebuild reads these off the symbol table; on a merged graph only the
    qualified names remain, so the owning type's members are found by name.
    """
    prefix = f"{owner.fully_qualified_name}.{name}"
    return [node for qname, node in call_graph.nodes.items() if qname == prefix or qname.startswith(f"{prefix}(")]


def override_nodes(call_graph: CallGraph, target: Node) -> list[Node]:
    """Same-named members on types that inherit the target's owner.

    A call through a base-typed reference resolves to the base declaration; the
    INHERITS edges already in the merged graph carry the relation the full rebuild
    reads from source.
    """
    if not target.is_callable():
        return []
    owner_qname = parent_qualified_name(target.fully_qualified_name)
    owner = call_graph.nodes.get(owner_qname)
    if owner is None:
        return []
    member = target.fully_qualified_name[len(owner_qname) + 1 :].split("(")[0]
    overrides: list[Node] = []
    for ref in call_graph.reference_edges:
        if ref.kind is EdgeKind.INHERITS and ref.dst == owner_qname:
            derived = call_graph.nodes.get(ref.src)
            if derived is not None:
                overrides.extend(members_named(call_graph, derived, member))
    return overrides


def targets_for(
    index: GraphIndex,
    file_path: str,
    line: int,
    character: int,
    kind: str,
    adapter: LanguageAdapter,
    site: CallSite,
) -> CallTargets:
    """Every node the call *site*, of *kind*, reaches when its definition is at this position.

    Mirrors the engine's own definition strategy: an argument position is a method
    group only when it resolves to something callable; a member read only when it
    resolves to a callable, and a member write only when it resolves to a setter; a
    ``foreach`` calls the enumerated type's ``GetEnumerator``; a collection initializer
    calls ``Add``; a base member dispatches to its overrides; a construction reaches the
    constructors.
    """
    declaration = index.declaration_at(file_path, line, character)
    if declaration is None:
        return CallTargets.none()
    if kind == METHOD_GROUP and not (
        declaration.is_callable()
        or declaration.is_class()
        or index.inspector.declares_function_value(Path(file_path), line, character)
    ):
        return CallTargets.none()
    if kind in (MEMBER_READ, MEMBER_WRITE) and not declaration.is_callable():
        return CallTargets.none()
    if kind == MEMBER_WRITE and not index.inspector.declares_setter(Path(file_path), line, character):
        return CallTargets.none()
    return targets_through(index, declaration, kind, adapter, site)


def targets_through(
    index: GraphIndex, declaration: Node, kind: str, adapter: LanguageAdapter, site: CallSite
) -> CallTargets:
    """Every node the call *site*, of *kind*, reaches through *declaration*, expanded as the full build expands it."""
    call_graph = index.call_graph
    constructing = adapter.expands_constructors and index.inspector.is_construction_site(site)
    through_base = index.inspector.names_base_member(site)
    resolved = _with_owning_class(index, declaration, include_owner=kind != ITERATED)
    targets: list[Node] = []
    for node in resolved:
        targets.append(node)
        dispatched = node.type in DISPATCHED_KINDS and not through_base
        if kind == ITERATED:
            targets.extend(members_named(call_graph, node, "GetEnumerator"))
        else:
            if adapter.expands_virtual_dispatch and dispatched:
                targets.extend(override_nodes(call_graph, node))
            if kind == COLLECTION_INITIALIZER:
                targets.extend(members_named(call_graph, node, "Add"))
        # Independent of *kind*: the engine decides this per site too, so a construction
        # that is also a collection initializer or a loop subject keeps its constructors.
        if constructing and node.is_class():
            targets.extend(members_named(call_graph, node, node.fully_qualified_name.split(".")[-1].split("<")[0]))
    implementations = [
        (node.file_path, node.line_start - 1, node.col_start)
        for node in resolved
        if node.type in DISPATCHED_KINDS and not through_base
    ]
    return CallTargets(targets, implementations)
