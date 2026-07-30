from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph, EdgeKind
from static_analyzer.node import Node
from static_analyzer.program_info.builder import build_program_information
from static_analyzer.program_info.models import Channel


def node(
    name: str,
    line: int,
    *,
    detail: str = "",
    selection_span: tuple[int, int, int, int] = (0, 0, 0, 0),
    parent_chain: tuple[tuple[str, int], ...] = (),
    tags: tuple[int, ...] = (),
    deprecated: bool = False,
) -> Node:
    return Node(
        name,
        NodeType.FUNCTION,
        "pkg/file.py",
        line,
        line + 2,
        detail=detail,
        selection_span=selection_span,
        parent_chain=parent_chain,
        tags=tags,
        deprecated=deprecated,
    )


def test_builder_retains_node_facts_and_old_cache_defaults():
    graph = CallGraph()
    enriched = node(
        "a",
        1,
        detail="(value: Item) -> Result",
        selection_span=(1, 4, 1, 5),
        parent_chain=(("Container", int(NodeType.CLASS)),),
        tags=(1,),
        deprecated=True,
    )
    old = node("b", 5)
    del old.detail
    del old.selection_span
    del old.parent_chain
    del old.tags
    del old.deprecated
    graph.add_node(enriched)
    graph.add_node(old)
    info = build_program_information(graph)
    assert info.symbols[0].detail == "(value: Item) -> Result"
    assert info.symbols[0].deprecated
    assert info.symbols[1].detail == ""
    assert info.symbols[1].selection_span == (0, 0, 0, 0)


def test_builder_aggregates_duplicate_reference_evidence():
    graph = CallGraph()
    graph.add_node(node("a", 1))
    graph.add_node(node("b", 5))
    graph.add_reference_edge("a", "b", EdgeKind.CONTAINS)
    graph.add_reference_edge("a", "b", EdgeKind.CONTAINS)
    edge = build_program_information(graph).edges[0]
    assert edge.channel == Channel.CONTAINS
    assert edge.count == 2
    assert edge.raw_weight == 2


def test_call_site_count_is_direct_and_projection_uses_minimum_one():
    graph = CallGraph()
    graph.add_node(node("a", 1))
    graph.add_node(node("b", 5))
    graph.add_edge("a", "b")
    assert graph.edges[0].call_site_count == 0
    assert graph.program_map_networkx()["a"]["b"]["weight"] == 1


def test_duplicate_call_sites_are_deduplicated_without_copying_for_count():
    graph = CallGraph()
    graph.add_node(node("a", 1))
    graph.add_node(node("b", 5))
    sites = [{"file": "x.py", "line": 2, "column": 3}] * 2
    graph.add_edge("a", "b", sites)
    assert graph.edges[0].call_site_count == 1
    assert graph.program_map_networkx()["a"]["b"]["call_count"] == 1


def test_builder_and_projection_are_insertion_order_deterministic():
    first = CallGraph()
    second = CallGraph()
    for graph, names in ((first, ("a", "b")), (second, ("b", "a"))):
        for name in names:
            graph.add_node(node(name, {"a": 1, "b": 10}[name]))
        graph.add_edge("a", "b", [{"line": 1}])
    first_info = build_program_information(first)
    second_info = build_program_information(second)
    assert first_info.symbols == second_info.symbols
    assert first_info.edges == second_info.edges
    assert list(first_info.projection().edges(data=True)) == list(second_info.projection().edges(data=True))
