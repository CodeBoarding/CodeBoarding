import math

import networkx as nx
import pytest

from static_analyzer.graph import ClusterResult
from static_analyzer.program_info.errors import InvalidClusterCoverError, ProgramInformationError
from static_analyzer.program_info.impact import ProgramDeltaSummary, summarize_delta
from static_analyzer.program_info.models import Channel, EdgeEvidence, ProgramInformation, SymbolFact
from static_analyzer.program_info.program_map import build_group_profiles
from static_analyzer.program_info.projection import from_projection, to_projection


def fact(name: str, path: str = "pkg/a.py") -> SymbolFact:
    return SymbolFact(name, 12, path, 1, 3, 2, f"detail {name}", (1, 2, 1, 5), (("Parent", 5),), (1,), False)


def rich_information() -> ProgramInformation:
    return ProgramInformation(
        tuple(sorted((fact("a"), fact("b"), fact("c", "other/c.py"), fact("d", "other/d.py")))),
        tuple(
            sorted(
                (
                    EdgeEvidence("a", "b", Channel.CALL, 3, 3.0),
                    EdgeEvidence("b", "a", Channel.TYPEREF, 1, 2.0),
                    EdgeEvidence("b", "c", Channel.CALL, 2, 2.0),
                    EdgeEvidence("c", "d", Channel.INHERITS, 1, 1.0),
                    EdgeEvidence("d", "c", Channel.IMPORT, 4, 4.0),
                )
            )
        ),
    )


def clusters() -> ClusterResult:
    return ClusterResult(
        clusters={10: {"a", "b"}, 20: {"c"}, 30: {"d"}},
        cluster_to_files={10: {"pkg/a.py"}, 20: {"other/c.py"}, 30: {"other/d.py"}},
        file_to_clusters={"pkg/a.py": {10}, "other/c.py": {20}, "other/d.py": {30}},
    )


def test_codec_round_trip_preserves_all_authoritative_facts_and_channels() -> None:
    information = rich_information()
    projection = to_projection(information)

    assert from_projection(projection) == information
    assert projection.graph["program_information_codec"] == 1
    assert projection["a"]["b"]["evidence"] == (("call", 3, 3.0),)
    assert projection.nodes["a"]["parent_chain"] == (("Parent", 5),)


@pytest.mark.parametrize("information", [ProgramInformation((), ()), ProgramInformation((fact("only"),), ())])
def test_codec_round_trip_handles_empty_and_singleton(information: ProgramInformation) -> None:
    assert from_projection(to_projection(information)) == information


def test_codec_legacy_defaults_are_concrete_and_restricted() -> None:
    graph = nx.DiGraph()
    graph.add_node("a", file_path="a.py")
    graph.add_node("b", file_path="b.py", type=7)
    graph.add_edge("a", "b", weight=2.5)

    decoded = from_projection(graph)

    assert decoded.symbol("a").kind == 0
    assert decoded.symbol("a").line_start == 0
    assert decoded.edges == (EdgeEvidence("a", "b", Channel.CALL, 1, 2.5),)


def test_codec_rejects_missing_versioned_attrs_and_untyped_malformed_edges() -> None:
    graph = nx.DiGraph(program_information_codec=1)
    graph.add_node("a", file_path="a.py", kind=1)
    with pytest.raises(ProgramInformationError, match="missing projection attrs"):
        from_projection(graph)

    legacy = nx.DiGraph()
    legacy.add_node("a", file_path="a.py")
    legacy.add_node("b", file_path="b.py")
    legacy.add_edge("a", "b", description="not evidence")
    with pytest.raises(ProgramInformationError, match="no typed evidence"):
        from_projection(legacy)


def test_profiles_describe_exact_fitted_groups_and_directed_crossing_flow() -> None:
    profiles = build_group_profiles(clusters(), rich_information(), [{10}, {20, 30}], limit=3)
    first, second = profiles

    assert first.cluster_ids == (10,)
    assert first.symbols == ("a", "b")
    assert (first.symbol_count, first.file_count, first.package_count) == (2, 1, 1)
    assert first.internal_flow == 4.0
    assert first.outgoing_flow == 2.0
    assert first.incoming_flow == 0.0
    assert first.outgoing_groups[0].group_id == 1
    assert first.outgoing_groups[0].channels == ((Channel.CALL, 2.0),)
    assert second.incoming_groups[0].group_id == 0
    assert second.cyclic_scc_count == 1
    assert second.entries == ("c",)
    assert first.hubs == ("b", "a")
    assert math.isclose(first.cohesion, 4 / 6)
    assert first.flow_entropy > 0
    assert 0 < first.flow_concentration < 1


def test_profiles_reject_each_cover_failure_with_specific_context() -> None:
    information = rich_information()
    result = clusters()
    with pytest.raises(InvalidClusterCoverError, match="unknown clusters.*99"):
        build_group_profiles(result, information, [{10, 99}, {20, 30}])
    with pytest.raises(InvalidClusterCoverError, match="duplicate group ownership.*10"):
        build_group_profiles(result, information, [{10}, {10, 20, 30}])
    with pytest.raises(InvalidClusterCoverError, match="omit leaf clusters.*30"):
        build_group_profiles(result, information, [{10}, {20}])
    with pytest.raises(InvalidClusterCoverError, match="Malformed fitted group"):
        build_group_profiles(result, information, [{10}, set(), {20, 30}])


def test_impact_summary_is_bounded_typed_and_deterministic() -> None:
    old = rich_information()
    new_edges = tuple(edge for edge in old.edges if edge.key != ("b", "c", Channel.CALL)) + (
        EdgeEvidence("a", "c", Channel.IMPORT, 1, 1.0),
    )
    new = ProgramInformation(old.symbols, tuple(sorted(new_edges)))
    delta = old.snapshot().compare(new.snapshot())

    summary = summarize_delta(delta, old, new, max_depth=2, limit=2)

    assert summary.changed_channels == (Channel.CALL, Channel.IMPORT)
    assert summary.added_edge_count == summary.removed_edge_count == 1
    assert len(summary.impacted_symbols) <= 2
    assert summary.impacted_symbols == tuple(
        sorted(summary.impacted_symbols, key=lambda item: (item.depth, item.qualified_name))
    )
    assert "channels call, import" in summary.llm_str()


def test_empty_delta_summary_does_not_add_llm_content() -> None:
    assert ProgramDeltaSummary().is_empty
    assert ProgramDeltaSummary().llm_str() == ""
