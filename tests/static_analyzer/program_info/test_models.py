from dataclasses import FrozenInstanceError

import pytest

from static_analyzer.program_info.errors import InvalidClusterCoverError, InvalidWeightError, UnknownEndpointError
from static_analyzer.program_info.models import Channel, EdgeEvidence, ProgramInformation, SymbolFact


def fact(name: str, file_path: str = "pkg/module.py", detail: str = "") -> SymbolFact:
    return SymbolFact(name, 12, file_path, 1, 4, detail=detail)


def information(*edges: EdgeEvidence) -> ProgramInformation:
    return ProgramInformation((fact("a"), fact("b"), fact("c", "other/c.py")), tuple(sorted(edges)))


def test_domain_values_are_immutable():
    symbol = fact("a")
    with pytest.raises(FrozenInstanceError):
        symbol.detail = "changed"


@pytest.mark.parametrize("weight", [-1.0, float("inf"), float("nan")])
def test_evidence_rejects_invalid_weight(weight: float):
    with pytest.raises(InvalidWeightError):
        EdgeEvidence("a", "b", Channel.CALL, raw_weight=weight)


def test_evidence_rejects_negative_count():
    with pytest.raises(InvalidWeightError):
        EdgeEvidence("a", "b", Channel.CALL, count=-1)


def test_unknown_edge_endpoint_is_rejected():
    with pytest.raises(UnknownEndpointError, match="missing"):
        ProgramInformation((fact("a"),), (EdgeEvidence("a", "missing", Channel.CALL),))


def test_duplicate_and_unsorted_symbols_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        ProgramInformation((fact("a"), fact("a")), ())
    with pytest.raises(ValueError, match="ordering"):
        ProgramInformation((fact("b"), fact("a")), ())


def test_projection_aggregates_channels_and_preserves_evidence():
    info = information(
        EdgeEvidence("a", "b", Channel.CALL, count=3, raw_weight=3),
        EdgeEvidence("a", "b", Channel.CONTAINS, count=2, raw_weight=2),
        EdgeEvidence("b", "c", Channel.IMPORT),
    )
    graph = info.projection({"contains"})
    assert list(graph.nodes) == ["a", "b", "c"]
    assert graph["a"]["b"] == {
        "call": 3.0,
        "call_count": 3,
        "contains": 2.0,
        "contains_count": 2,
        "weight": 5.0,
    }
    assert not graph.has_edge("b", "c")


def test_projection_defaults_to_all_structural_channels():
    info = information(EdgeEvidence("a", "b", Channel.TYPEREF), EdgeEvidence("b", "c", Channel.IMPORT))
    graph = info.projection()
    assert graph["a"]["b"]["weight"] == 0.5
    assert graph["b"]["c"]["weight"] == 0.25


def test_statistics_include_isolates_channels_and_multiplicity():
    stats = information(EdgeEvidence("a", "b", Channel.CALL, count=4, raw_weight=4)).statistics
    assert stats.symbol_count == 3
    assert stats.edge_count == 1
    assert stats.evidence_count == 4
    assert stats.total_weight == 4
    assert stats.isolated_symbols == 1
    assert stats.channel_counts == ((Channel.CALL, 4),)


def test_symbol_profiles_distinguish_calls_and_structure():
    info = information(
        EdgeEvidence("a", "b", Channel.CALL, count=2, raw_weight=2),
        EdgeEvidence("c", "b", Channel.INHERITS),
    )
    profiles = {profile.qualified_name: profile for profile in info.symbol_profiles()}
    assert profiles["a"].callee_count == 1
    assert profiles["b"].caller_count == 1
    assert profiles["b"].structural_neighbor_count == 1
    assert profiles["b"].weighted_fan_in == 3.25


def test_cluster_profiles_measure_internal_and_crossing_flow():
    info = information(
        EdgeEvidence("a", "b", Channel.CALL, count=2, raw_weight=2),
        EdgeEvidence("b", "c", Channel.CALL),
    )
    profiles = info.cluster_profiles({1: {"a", "b"}, 2: {"c"}})
    first = profiles[0]
    assert first.internal_weight == 2
    assert first.outgoing_weight == 1
    assert first.incoming_weight == 0
    assert first.cohesion == 1
    assert first.coupling == pytest.approx(1 / 3)
    assert first.lens.boundary_symbols == ("b",)
    assert first.lens.hubs == ("b", "a")


@pytest.mark.parametrize(
    "clusters",
    [
        {1: {"a", "b"}},
        {1: {"a", "b", "c", "missing"}},
        {1: {"a", "b"}, 2: {"b", "c"}},
    ],
)
def test_cluster_profiles_require_exact_cover(clusters: dict[int, set[str]]):
    with pytest.raises(InvalidClusterCoverError):
        information().cluster_profiles(clusters)


def test_snapshot_fingerprint_is_stable_and_order_sensitive_facts_are_normalized():
    first = information(EdgeEvidence("a", "b", Channel.CALL)).snapshot()
    second = information(EdgeEvidence("a", "b", Channel.CALL)).snapshot()
    assert first.fingerprint == second.fingerprint
    assert fact("a").fingerprint() == fact("a").fingerprint()


def test_snapshot_delta_reports_each_change_class():
    old = ProgramInformation(
        (fact("a"), fact("b"), fact("removed")),
        (
            EdgeEvidence("a", "b", Channel.CALL),
            EdgeEvidence("b", "removed", Channel.IMPORT),
        ),
    ).snapshot()
    new = ProgramInformation(
        (fact("a", detail="new signature"), fact("added"), fact("b")),
        (
            EdgeEvidence("a", "b", Channel.CALL, count=2, raw_weight=2),
            EdgeEvidence("b", "added", Channel.TYPEREF),
        ),
    ).snapshot()
    delta = old.compare(new)
    assert delta.added_symbols == ("added",)
    assert delta.removed_symbols == ("removed",)
    assert delta.changed_symbols == ("a",)
    assert delta.changed_edges == (("a", "b", Channel.CALL),)
    assert delta.added_edges == (("b", "added", Channel.TYPEREF),)
    assert delta.removed_edges == (("b", "removed", Channel.IMPORT),)
    assert delta.statistics_changed
    assert not delta.is_empty


def test_identical_snapshots_have_empty_delta():
    snapshot = information().snapshot()
    assert snapshot.compare(snapshot).is_empty


def test_empty_program_information_is_valid():
    info = ProgramInformation((), ())
    assert info.statistics.symbol_count == 0
    assert info.projection().number_of_nodes() == 0
    assert info.cluster_profiles({}) == ()
