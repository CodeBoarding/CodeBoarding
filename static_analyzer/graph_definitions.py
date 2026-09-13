"""Resolve a definition position against a merged call graph, expanded the way the engine expands it.

Shared by the engine's symbol index, the warm-start restore of cached edges, and the linker
of answers an engine could not name, so none of them can disagree on which nodes a call
site reaches.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from static_analyzer.cfg import CallGraph, EdgeKind
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_client import LSPClient
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
# Answers rather than call sites: what a call's target is implemented by, and a receiver whose member a call names.
IMPLEMENTATION = "implementation"
RECEIVER = "receiver"
# A dispatched declaration a call reached inside its engine, whose overrides may lie in files that engine never read.
OVERRIDE = "override"


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


def resolve_declaration[T](
    inspector: SourceInspector,
    file_path: Path,
    line: int,
    character: int,
    declared_at: Callable[[int, int], T | None],
    declared_named: Callable[[str], list[T]],
) -> T | None:
    """The declaration a definition result at this position names, by the rules both resolvers share.

    Exact position, an overload signature standing for its implementation; else the declaration at
    the function literal a name there is bound to; else the sole callable or class the file declares
    under the name declared there. Anything else matches nothing.
    """
    line, character = inspector.overload_implementations(file_path).get((line, character), (line, character))
    found = declared_at(line, character)
    if found is None:
        literal = inspector.function_values(file_path).get((line, character))
        found = declared_at(*literal) if literal is not None else None
    if found is not None:
        return found
    name = inspector.declared_name_at(file_path, line, character)
    candidates = declared_named(name) if name else []
    return candidates[0] if len(candidates) == 1 else None


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

    @cached_property
    def derived(self) -> dict[str, list[Node]]:
        """The types the graph's INHERITS edges derive from each type, by the base's qualified name."""
        derived: dict[str, list[Node]] = defaultdict(list)
        for ref in self.call_graph.reference_edges:
            node = self.call_graph.nodes.get(ref.src)
            if ref.kind is EdgeKind.INHERITS and node is not None:
                derived[ref.dst].append(node)
        return derived

    def declaration_at(self, file_path: str, line: int, character: int) -> Node | None:
        """The node a definition result names, by ``resolve_declaration``."""
        nodes = self.nodes_in(file_path)
        if not nodes:
            return None
        return resolve_declaration(
            self.inspector,
            Path(file_path),
            line,
            character,
            lambda at_line, at_character: _innermost(
                node for node in nodes if (node.line_start, node.col_start) == (at_line + 1, at_character)
            ),
            lambda name: list(self._declarations_by_name(file_path).get(name, {}).values()),
        )

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


def _innermost(nodes: Iterable[Node]) -> Node | None:
    """The last-starting, shortest-spanning, most specific of the candidates."""
    matches = list(nodes)
    if not matches:
        return None
    return max(
        matches,
        key=lambda node: (node.line_start, node.col_start, -node.line_end, len(node.fully_qualified_name)),
    )


def _with_owning_class(index: GraphIndex, target: Node, include_owner: bool) -> list[Node]:
    """The declaration, and the class it belongs to when a call on it reaches that too."""
    if not ((include_owner and target.is_callable()) or target.type.name == "CONSTRUCTOR"):
        return [target]
    owner = index.call_graph.nodes.get(parent_qualified_name(target.fully_qualified_name))
    return [target, owner] if owner is not None and owner.is_class() else [target]


def implemented_by(
    index: GraphIndex, client: LSPClient, declarations: Collection[tuple[str, int, int]]
) -> dict[tuple[str, int, int], list[Node]]:
    """The nodes implementing each declaration, keyed by the position it is declared at."""
    positions = list(declarations)
    answers = client.send_implementation_batch(
        [(Path(file_path), line, character) for file_path, line, character in positions]
    )
    found: dict[tuple[str, int, int], list[Node]] = {}
    for position, implementations in zip(positions, answers):
        nodes: list[Node] = []
        for implementation in implementations:
            location = definition_location(implementation)
            declaration = index.declaration_at(str(location[0]), location[1], location[2]) if location else None
            if declaration is not None:
                nodes.extend(_with_owning_class(index, declaration, include_owner=False))
        if nodes:
            found[position] = nodes
    return found


def members_named(call_graph: CallGraph, owner: Node, name: str) -> list[Node]:
    """Nodes for ``owner``'s members called *name*, by qualified-name prefix.

    The full rebuild reads these off the symbol table; on a merged graph only the
    qualified names remain, so the owning type's members are found by name.
    """
    prefix = f"{owner.fully_qualified_name}.{name}"
    return [node for qname, node in call_graph.nodes.items() if qname == prefix or qname.startswith(f"{prefix}(")]


def override_nodes(index: GraphIndex, target: Node) -> list[Node]:
    """Same-named members on types that inherit the target's owner.

    A call through a base-typed reference resolves to the base declaration; the
    INHERITS edges already in the merged graph carry the relation the full rebuild
    reads from source.
    """
    if not target.is_callable():
        return []
    owner_qname = parent_qualified_name(target.fully_qualified_name)
    if owner_qname not in index.call_graph.nodes:
        return []
    member = target.fully_qualified_name[len(owner_qname) + 1 :].split("(")[0]
    return [
        override
        for derived in index.derived.get(owner_qname, [])
        for override in members_named(index.call_graph, derived, member)
    ]


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
    constructors; an implementation answer reaches only the declaration it names.
    """
    declaration = index.declaration_at(file_path, line, character)
    if declaration is None:
        return CallTargets.none()
    if kind == IMPLEMENTATION:
        return CallTargets(_with_owning_class(index, declaration, include_owner=False), [])
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
                targets.extend(override_nodes(index, node))
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
