"""Units and the trie of their qualified-name prefixes.

A unit is one file: the set of qualified names the engine declared in it. Its *position* is
the longest prefix its names share, read from the names alone (the engine emits no module
node, and a file path is never parsed here). The trie over every unit's position is the
directory tree in every language today, because every adapter derives the prefix from the
path; a declared namespace would land in the same structure the day an adapter carried one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from static_analyzer.cfg import CallGraph
from static_analyzer.clustering.names.tokens import segments


@dataclass(frozen=True)
class Unit:
    """One file's declarations. ``unit_id`` is the engine's path, used only as an identity."""

    unit_id: str
    language: str
    names: tuple[str, ...]
    position: tuple[str, ...]
    key: tuple[str, ...]
    """``position`` plus the one symbol the file declares, when it declares one: what a rule owns
    to claim this file and not a sibling declared in the same module."""


def unit_key(names: Iterable[str], delimiter: str) -> tuple[str, ...]:
    """The longest prefix the names share."""
    shared: list[str] = []
    for parts in zip(*(tuple(segments(name, delimiter)) for name in names)):
        if len(set(parts)) != 1:
            break
        shared.append(parts[0])
    return tuple(shared)


def unit_position(names: Iterable[str], delimiter: str) -> tuple[str, ...]:
    """The unit key less a trailing symbol.

    A file declaring one class shares that class's name across every member, so the bare
    prefix would name the class rather than the module it sits in.
    """
    listed = list(names)
    key = unit_key(listed, delimiter)
    return key[:-1] if any(tuple(segments(name, delimiter)) == key for name in listed) else key


def units_from_graph(graph: CallGraph, language: str) -> list[Unit]:
    by_file: dict[str, list[str]] = {}
    for qualified_name, node in graph.nodes.items():
        if node.file_path:
            by_file.setdefault(node.file_path, []).append(qualified_name)
    return [
        Unit(
            file_path,
            language,
            tuple(sorted(names)),
            unit_position(names, graph.delimiter),
            unit_key(names, graph.delimiter),
        )
        for file_path, names in sorted(by_file.items())
    ]


def units_from_graphs(graphs: Mapping[str, CallGraph]) -> list[Unit]:
    units: list[Unit] = []
    for language in sorted(graphs):
        units.extend(units_from_graph(graphs[language], language))
    return units


@dataclass
class TrieNode:
    path: tuple[str, ...]
    children: dict[str, TrieNode] = field(default_factory=dict)
    units: list[Unit] = field(default_factory=list)
    count: int = 0
    """Units in this subtree, set once the trie is built."""


class Trie:
    """The prefix tree of unit positions.

    A node holding one unit and nothing else is usually that unit's own name (a Python
    module, a file declaring several C# types); the walk treats such a node as a loose unit
    of its parent rather than as a scope, but keeps it in the tree, because a one-file
    feature directory under a layer is evidence the transposition needs.
    """

    def __init__(self, units: Iterable[Unit]):
        self.root = TrieNode(())
        for unit in units:
            node = self.root
            for depth, segment in enumerate(unit.position):
                node = node.children.setdefault(segment, TrieNode(unit.position[: depth + 1]))
            node.units.append(unit)
        _count(self.root)

    def node(self, path: tuple[str, ...]) -> TrieNode | None:
        node = self.root
        for segment in path:
            child = node.children.get(segment)
            if child is None:
                return None
            node = child
        return node


def _count(node: TrieNode) -> int:
    node.count = len(node.units) + sum(_count(child) for child in node.children.values())
    return node.count
