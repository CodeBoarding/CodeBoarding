"""Resolve a definition position against a merged call graph, expanded the way the engine expands it.

Shared by the warm-start updater (definitions of changed files, against the cached
graph) and the cross-engine linker (definitions one engine could not name, against
every engine's graph), so the two cannot disagree on which nodes a call site reaches.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
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


class MatchRule(StrEnum):
    """How a definition position was matched to the declaration it names."""

    EXACT = "exact"
    SIGNATURE = "signature"
    """Inside the declaration's signature: its own line, past its name, outside its body."""
    NAME = "name"
    """The identifier written at the definition names exactly one declaration in the file."""
    NONE = "none"


@dataclass(frozen=True)
class Match[T]:
    """A declaration a definition position resolved to, and the rule that found it."""

    declaration: T | None
    rule: MatchRule

    @classmethod
    def none(cls) -> "Match[T]":
        return cls(None, MatchRule.NONE)


@dataclass(frozen=True)
class CallTargets:
    """What a call site reaches: the nodes the graph already holds, and the queries still owed.

    ``implementations`` are declaration positions a server still has to expand. A full build
    follows every callable target with ``textDocument/implementation`` and gives the caller an
    edge to each result, so a resolver that stopped at the definition would answer with a
    strictly smaller set than the build it mirrors.
    """

    nodes: list[Node]
    implementations: list[tuple[str, int, int]]

    @classmethod
    def none(cls) -> "CallTargets":
        return cls([], [])


def is_method_group_target(match: Match, declared_callable: bool) -> bool:
    """Whether a name passed as a value denotes something callable rather than an ordinary value.

    Why a signature match never counts: it means the position fell inside a declaration the
    index holds while the thing actually named there -- an enum member, a property, a local --
    is one it does not, so the enclosing declaration would stand in for a value.
    """
    return match.declaration is not None and match.rule is not MatchRule.SIGNATURE and declared_callable


class MatchCounts:
    """How a pass over definition results matched them, for one summary line at the end."""

    def __init__(self) -> None:
        self.by_rule: Counter[MatchRule] = Counter()

    def record(self, rule: MatchRule) -> None:
        self.by_rule[rule] += 1

    def summary(self) -> str:
        return (
            f"{self.by_rule[MatchRule.EXACT]} exact, "
            f"{self.by_rule[MatchRule.SIGNATURE]} in a declaration's signature, "
            f"{self.by_rule[MatchRule.NAME]} by the name at the definition, "
            f"{self.by_rule[MatchRule.NONE]} rejected"
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

    def declaration_at(self, file_path: str, line: int, character: int) -> Match[Node]:
        """The node a definition result names, by the engine's matching rules.

        Exact position, else the node whose signature covers the position on its own line,
        else the sole callable or class declared in the file under the name written there --
        the overload set a server answers with a signature line for. Anything else is
        ambiguous and matches nothing.
        """
        exact = _innermost(
            node for node in self.nodes_in(file_path) if (node.line_start, node.col_start) == (line + 1, character)
        )
        if exact is not None:
            return self._matched(exact, MatchRule.EXACT)

        signature = _innermost(
            node
            for node in self.nodes_in(file_path)
            if node.line_start == line + 1
            and position_inside_node(node, line, character)
            and not self.inspector.in_declaration_body(
                Path(file_path), (node.line_start - 1, node.col_start), line, character
            )
        )
        if signature is not None:
            return self._matched(signature, MatchRule.SIGNATURE)

        named = self._sole_declaration_named(file_path, line, character)
        if named is not None:
            return self._matched(named, MatchRule.NAME)

        self.counts.record(MatchRule.NONE)
        return Match.none()

    def _matched(self, node: Node, rule: MatchRule) -> Match[Node]:
        self.counts.record(rule)
        return Match(node, rule)

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
    # ``col_end`` is 0 on a node built before it was recorded, which reads as unbounded.
    if line == node.line_end and node.col_end and character > node.col_end:
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
    target = index.declaration_at(file_path, line, character).declaration
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


def implementation_positions(nodes: Iterable[Node]) -> list[tuple[str, int, int]]:
    """Where to ask which implementations override each callable among *nodes*.

    A full build follows every callable it resolves with ``textDocument/implementation``;
    the query belongs at the declaration's own name position, in LSP coordinates.
    """
    return [(node.file_path, node.line_start - 1, node.col_start) for node in nodes if node.is_callable()]


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
) -> CallTargets:
    """Every node a call site of *kind* reaches when its definition is at this position.

    Mirrors the engine's own definition strategy: an argument position is a method
    group only when it resolves to something callable; a ``foreach`` calls the
    enumerated type's ``GetEnumerator``; a collection initializer calls ``Add``; a
    base member dispatches to its overrides; a construction reaches the constructors.
    """
    call_graph = index.call_graph
    match = index.declaration_at(file_path, line, character)
    if match.declaration is None:
        return CallTargets.none()
    if kind == METHOD_GROUP and not is_method_group_target(
        match,
        match.declaration.is_callable()
        or match.declaration.is_class()
        or index.inspector.declares_function_value(Path(file_path), line, character),
    ):
        return CallTargets.none()
    resolved = _with_owning_class(index, match.declaration, include_owner=kind != ITERATED)
    targets: list[Node] = []
    for node in resolved:
        targets.append(node)
        if kind == ITERATED:
            targets.extend(members_named(call_graph, node, "GetEnumerator"))
        else:
            if adapter.expands_virtual_dispatch:
                targets.extend(override_nodes(call_graph, node))
            if kind == COLLECTION_INITIALIZER:
                targets.extend(members_named(call_graph, node, "Add"))
        # Independent of *kind*: the engine decides this per site too, so a construction
        # that is also a collection initializer or a loop subject keeps its constructors.
        if constructing and adapter.expands_constructors and node.is_class():
            targets.extend(members_named(call_graph, node, node.fully_qualified_name.split(".")[-1].split("<")[0]))
    return CallTargets(targets, implementation_positions(resolved))
