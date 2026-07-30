import math

import networkx as nx
import pytest

from static_analyzer.program_info import (
    Channel,
    EdgeEvidence,
    ProgramInformation,
    SymbolFact,
    analyze_flow,
    analyze_modules,
    analyze_topology,
)
from static_analyzer.program_info.errors import InvalidClusterCoverError


def fact(name: str, path: str = "pkg/file.py") -> SymbolFact:
    return SymbolFact(name, 12, path, 1, 2)


def information() -> ProgramInformation:
    return ProgramInformation(
        tuple(sorted((fact("a"), fact("b"), fact("c", "other/c.py"), fact("d", "other/d.py")))),
        tuple(
            sorted(
                (
                    EdgeEvidence("a", "b", Channel.CALL, 3, 3),
                    EdgeEvidence("b", "a", Channel.TYPEREF),
                    EdgeEvidence("b", "c", Channel.CALL),
                    EdgeEvidence("b", "c", Channel.IMPORT),
                )
            )
        ),
    )


def test_topology_finds_cycles_layers_sources_sinks_and_isolates() -> None:
    topology = analyze_topology(information().projection())
    assert topology.regions[0].members == ("a", "b")
    assert topology.regions[0].cyclic
    assert topology.maximum_depth == 1
    assert topology.sources == ("d",)
    assert topology.sinks == ("c", "d")
    assert topology.bridges == ("b",)


def test_topology_is_insertion_order_independent() -> None:
    first = nx.DiGraph([("z", "a"), ("a", "z"), ("z", "x")])
    second = nx.DiGraph(reversed(list(first.edges)))
    second.add_nodes_from(reversed(list(first.nodes)))
    assert analyze_topology(first) == analyze_topology(second)


def test_empty_and_singleton_topology() -> None:
    assert analyze_topology(nx.DiGraph()).maximum_depth == 0
    graph = nx.DiGraph()
    graph.add_edge("only", "only")
    result = analyze_topology(graph)
    assert result.regions[0].cyclic
    assert result.sources == ()
    assert result.sinks == ()


def test_flow_computes_ratios_entropy_concentration_and_stable_tops() -> None:
    result = analyze_flow(information().edges, {"a", "b"}, limit=1)
    assert result.internal_weight == 3.5
    assert result.crossing_weight == 1.25
    assert result.internal_ratio == pytest.approx(3.5 / 4.75)
    assert result.entropy > 0
    assert 0 < result.concentration < 1
    assert result.top_incoming == (("b", 3.0),)
    assert result.top_outgoing == (("a", 3.0),)


def test_empty_flow_is_finite() -> None:
    result = analyze_flow((), set())
    assert result.total_weight == 0
    assert result.entropy == 0
    assert result.concentration == 0
    assert all(math.isfinite(value) for value in (result.internal_ratio, result.entropy, result.concentration))


def test_exact_modules_include_crossing_channel_composition() -> None:
    result = analyze_modules(information(), {7: {"a", "b"}, 9: {"c", "d"}})
    left, right = result.profiles
    assert left.module_id == 7
    assert left.files == ("pkg/file.py",)
    assert left.entry_symbols == ()
    assert left.exit_symbols == ("b",)
    assert left.boundary_symbols == ("b",)
    assert right.entry_symbols == ("c",)
    assert result.inter_module_flow[0].source_module == 7
    assert result.inter_module_flow[0].channels == ((Channel.CALL, 1.0), (Channel.IMPORT, 0.25))


def test_exact_and_partial_cover_are_explicitly_distinct() -> None:
    with pytest.raises(InvalidClusterCoverError, match="omits"):
        analyze_modules(information(), {1: {"a", "b"}})
    partial = analyze_modules(information(), {1: {"a", "b"}}, exact=False)
    assert partial.profiles[0].members == ("a", "b")


def test_modules_reject_unknown_and_duplicate_members() -> None:
    with pytest.raises(InvalidClusterCoverError, match="unknown"):
        analyze_modules(information(), {1: {"a", "unknown"}}, exact=False)
    with pytest.raises(InvalidClusterCoverError, match="duplicate"):
        analyze_modules(information(), {1: {"a"}, 2: {"a"}}, exact=False)
