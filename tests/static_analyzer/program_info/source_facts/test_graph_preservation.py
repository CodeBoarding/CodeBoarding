from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph, EdgeKind
from static_analyzer.node import Node


def node(name: str, file: str, line: int) -> Node:
    return Node(name, NodeType.FUNCTION, file, line, line + 2)


def graph() -> CallGraph:
    result = CallGraph(language="python")
    for value in (node("a", "a.py", 0), node("b", "b.py", 0), node("c", "c.py", 0)):
        result.add_node(value)
    result.add_edge("a", "b", ({"file": "a.py", "line": 2, "column": 3},))
    result.add_reference_edge("a", "b", EdgeKind.TYPEREF)
    result.add_reference_edge("b", "c", EdgeKind.IMPORT)
    return result


def test_filter_preserves_surviving_structural_kinds_and_removes_dangling_edges():
    original = graph()
    dropped = []
    filtered = original.filter(lambda value: value.fully_qualified_name != "c", dropped.append)
    assert [(edge.get_source(), edge.get_destination()) for edge in filtered.edges] == [("a", "b")]
    assert filtered.reference_edges == [("a", "b", "typeref")]
    assert dropped == []
    assert filtered.nodes["a"].methods_called_by_me == {"b"}


def test_filter_by_files_preserves_source_fact_attributes():
    original = graph()
    original.nodes["a"].visibility = "public"
    original.nodes["a"].modifiers = ("async",)
    original.nodes["a"].annotations = ("route",)
    original.nodes["a"].type_use_evidence = ("Widget",)
    filtered = original.filter_by_files({"a.py", "b.py"})
    assert filtered.nodes["a"] is original.nodes["a"]
    assert filtered.nodes["a"].visibility == "public"
    assert filtered.nodes["a"].modifiers == ("async",)
    assert filtered.nodes["a"].annotations == ("route",)
    assert filtered.nodes["a"].type_use_evidence == ("Widget",)
    assert filtered.reference_edges == [("a", "b", "typeref")]


def test_union_preserves_both_structural_edge_sets_and_deduplicates():
    cached = graph().filter_by_files({"a.py", "b.py"})
    fresh = CallGraph(language="python")
    fresh.add_node(node("b", "b.py", 0))
    fresh.add_node(node("c", "c.py", 0))
    fresh.add_reference_edge("b", "c", EdgeKind.IMPORT)
    merged = cached.union(fresh)
    assert sorted(merged.nodes) == ["a", "b", "c"]
    assert merged.reference_edges == [("a", "b", "typeref"), ("b", "c", "import")]


def test_alias_promotion_is_respected_when_carrying_reference_edges():
    original = CallGraph()
    original.add_node(node("short", "a.py", 0))
    original.add_node(node("target", "b.py", 0))
    original.add_reference_edge("short", "target", EdgeKind.IMPORT)
    original.add_node(node("package.module.short", "a.py", 0))
    filtered = original.filter(lambda _: True, lambda _: None)
    assert sorted(filtered.nodes) == ["package.module.short", "target"]
    assert filtered.reference_edges == [("package.module.short", "target", "import")]
