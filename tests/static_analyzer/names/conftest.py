"""Builders shared by the names tests: units straight from qualified names, no engine."""

from static_analyzer.cfg import CallGraph
from pathlib import Path

from static_analyzer.clustering.names import ComponentRule, ScopeSpec, TreeSpec, Trie, Unit
from static_analyzer.clustering.names.inventory import TrieNode
from static_analyzer.config import NodeType
from static_analyzer.node import Node


def unit(file_path: str, *names: str, language: str = "python", project: bool = False) -> Unit:
    """A unit positioned by its path, the way the inventory positions a file under the repository root."""
    relative = Path(file_path)
    position = () if relative.parent.parts == (".",) else relative.parent.parts
    return Unit(file_path, language, tuple(sorted(names)), position, position + (relative.stem,), project)


def units_from_layout(layout: dict[str, list[str]], language: str = "python") -> list[Unit]:
    """``{file_path: [qualified names]}`` -> units, sorted the way the inventory sorts them."""
    return [unit(path, *names, language=language) for path, names in sorted(layout.items())]


def graph_from_layout(layout: dict[str, list[str]], language: str = "python") -> CallGraph:
    graph = CallGraph(language=language)
    for path, names in layout.items():
        for index, name in enumerate(names):
            kind = NodeType.CLASS if name.split(".")[-1][:1].isupper() else NodeType.FUNCTION
            graph.add_node(Node(name, kind, path, index + 1, index + 2))
    return graph


def scope_of(spec: TreeSpec, scope_id: str) -> ScopeSpec:
    scope = spec.scope(scope_id)
    assert scope is not None, f"scope {scope_id!r} was not drafted"
    return scope


def rule_of(scope: ScopeSpec, component_id: str) -> ComponentRule:
    rule = scope.rule(component_id)
    assert rule is not None, f"no rule {component_id!r} in scope {scope.scope_id!r}"
    return rule


def node_of(trie: Trie, path: tuple[str, ...]) -> TrieNode:
    node = trie.node(path)
    assert node is not None, f"no trie node at {path!r}"
    return node
