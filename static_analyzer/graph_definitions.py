"""Resolve a definition position against a merged call graph, expanded the way the engine expands it.

Shared by the warm-start updater (definitions of changed files, against the cached
graph) and the cross-engine linker (definitions one engine could not name, against
every engine's graph), so the two cannot disagree on which nodes a call site reaches.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from static_analyzer.cfg import CallGraph, EdgeKind
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.utils import definition_location
from static_analyzer.internal_references import parent_qualified_name, simple_name
from static_analyzer.node import Node

# How a call site was found in source; it decides what a resolved definition may become.
CALL = "call"
METHOD_GROUP = "method_group"
COLLECTION_INITIALIZER = "collection"
ITERATED = "iterated"


@dataclass
class MatchCounts:
    """How a pass over definition results matched them, for one summary line at the end."""

    exact: int = 0
    same_line: int = 0
    name_at_definition: int = 0
    rejected: int = 0

    def summary(self) -> str:
        return (
            f"{self.exact} exact, {self.same_line} containing the position, "
            f"{self.name_at_definition} by the name at the definition, {self.rejected} rejected"
        )


class GraphIndex:
    """A graph's nodes by file, so a position lookup does not scan every node."""

    def __init__(self, call_graph: CallGraph, inspector: SourceInspector) -> None:
        self.call_graph = call_graph
        self.counts = MatchCounts()
        self.inspector = inspector
        self._by_file: dict[str, list[Node]] = defaultdict(list)
        for node in call_graph.nodes.values():
            self._by_file[node.file_path].append(node)
        self._by_name: dict[str, dict[str, dict[tuple[int, int], Node]]] = {}

    def nodes_in(self, file_path: str) -> list[Node]:
        return self._by_file.get(file_path, [])

    def declaration_at(self, file_path: str, line: int, character: int) -> Node | None:
        """The node a definition result names, by the engine's matching rules.

        Exact position, else the innermost node declared on that line that still contains
        the position, else the sole callable or class declared in the file under the name
        written there -- the overload set a server answers with a signature line for.
        Anything else is ambiguous and matches nothing.
        """
        exact = _innermost(
            node for node in self.nodes_in(file_path) if (node.line_start, node.col_start) == (line + 1, character)
        )
        if exact is not None:
            self.counts.exact += 1
            return exact

        containing = _innermost(
            node
            for node in self.nodes_in(file_path)
            if node.line_start == line + 1 and position_inside_node(node, line, character)
        )
        if containing is not None:
            self.counts.same_line += 1
            return containing

        named = self._sole_declaration_named(file_path, line, character)
        if named is not None:
            self.counts.name_at_definition += 1
            return named

        self.counts.rejected += 1
        return None

    def _sole_declaration_named(self, file_path: str, line: int, character: int) -> Node | None:
        name = self.inspector.identifier_at(Path(file_path), line, character)
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
    """
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
    targets = [target]
    if (include_callable_parent and target.is_callable()) or target.type.name == "CONSTRUCTOR":
        parent = index.call_graph.nodes.get(parent_qualified_name(target.fully_qualified_name))
        if parent is not None and parent.is_class():
            targets.append(parent)
    return targets


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
    constructing: bool = False,
) -> list[Node]:
    """Every node a call site of *kind* reaches when its definition is at this position.

    Mirrors the engine's own definition strategy: an argument position is a method
    group only when it resolves to something callable; a ``foreach`` calls the
    enumerated type's ``GetEnumerator``; a collection initializer calls ``Add``; a
    base member dispatches to its overrides; a construction reaches the constructors.
    """
    call_graph = index.call_graph
    include_parent = kind != ITERATED
    resolved = nodes_at_location(index, file_path, line, character, include_parent)
    if kind == METHOD_GROUP and resolved:
        # A probe resolves to whatever declaration the name denotes; only a callable or a type
        # declared exactly there is a method group. An enum member or property is not a node,
        # and the type that encloses it must not stand in for it.
        primary = resolved[0]
        declared_here = (primary.line_start, primary.col_start) == (line + 1, character)
        if not declared_here or not (
            primary.is_callable()
            or primary.is_class()
            or index.inspector.declares_function_value(Path(file_path), line, character)
        ):
            return []
    targets: list[Node] = []
    for node in resolved:
        targets.append(node)
        if kind == ITERATED:
            targets.extend(members_named(call_graph, node, "GetEnumerator"))
            continue
        if adapter.expands_virtual_dispatch:
            targets.extend(override_nodes(call_graph, node))
        if kind == COLLECTION_INITIALIZER:
            targets.extend(members_named(call_graph, node, "Add"))
        if constructing and adapter.expands_constructors and node.is_class():
            targets.extend(members_named(call_graph, node, node.fully_qualified_name.split(".")[-1].split("<")[0]))
    return targets
