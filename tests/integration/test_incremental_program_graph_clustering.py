"""Recorded end-to-end incremental ProgramGraph and clustering scenarios."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from static_analyzer.infomap_clustering import HierarchicalInfomapClusterer
from static_analyzer.program_graph import ProgramGraph


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "cfg_cluster_commit_pairs"
MANIFESTS = sorted(FIXTURE_ROOT.glob("*/manifest.json"))
RecordedGraphs = tuple[ProgramGraph, ProgramGraph, ProgramGraph, set[str], dict]


def _params():
    if not MANIFESTS:
        return [pytest.param(None, marks=pytest.mark.skip(reason="No commit-pair clustering fixtures recorded yet"))]
    return [pytest.param(path, id=path.parent.name) for path in MANIFESTS]


def _edge_keys(graph: ProgramGraph) -> set[tuple[str, str, str]]:
    return {(edge.kind.value, edge.source, edge.target) for edge in graph.edges}


def _owner(clusters: dict[int, set[str]], node_id: str) -> int:
    owners = [cluster_id for cluster_id, members in clusters.items() if node_id in members]
    assert len(owners) == 1, f"Expected exactly one owner for {node_id!r}, got {owners}"
    return owners[0]


def _assert_graph_changes(base: ProgramGraph, head: ProgramGraph, expected: dict) -> None:
    added_nodes = set(head.nodes) - set(base.nodes)
    removed_nodes = set(base.nodes) - set(head.nodes)
    added_edges = _edge_keys(head) - _edge_keys(base)
    removed_edges = _edge_keys(base) - _edge_keys(head)
    assert len(added_nodes) == expected["added_node_count"]
    assert len(removed_nodes) == expected["removed_node_count"]
    assert len(added_edges) == expected["added_edge_count"]
    assert len(removed_edges) == expected["removed_edge_count"]
    assert set(expected.get("added_nodes", [])) <= added_nodes
    assert set(expected.get("removed_nodes", [])) <= removed_nodes
    assert {tuple(item) for item in expected.get("added_edges", [])} <= added_edges
    assert {tuple(item) for item in expected.get("removed_edges", [])} <= removed_edges
    for item in expected.get("changed_node_locations", []):
        node_id = item["node"]
        assert [base.nodes[node_id].line_start, base.nodes[node_id].line_end] == item["base"]
        assert [head.nodes[node_id].line_start, head.nodes[node_id].line_end] == item["head"]


def _assert_clustering(base_result, head_result, expected: dict) -> None:
    base_clusters = base_result.clusters
    head_clusters = head_result.clusters
    assert len(base_clusters) == expected["base_cluster_count"]
    assert len(head_clusters) == expected["head_cluster_count"]
    for item in expected.get("joins_existing", []):
        assert _owner(head_clusters, item["node"]) == _owner(head_clusters, item["anchor"])
    for item in expected.get("stable", []):
        assert _owner(base_clusters, item) == _owner(head_clusters, item)
    for item in expected.get("new_clusters", []):
        owners = {_owner(head_clusters, node_id) for node_id in item["nodes"]}
        assert len(owners) == 1
        assert all(next(iter(owners)) != _owner(head_clusters, anchor) for anchor in item.get("distinct_from", []))


def _assert_scope_stability(
    base: ProgramGraph,
    incremental: ProgramGraph,
    base_clusters: dict[int, set[str]],
    incremental_clusters: dict[int, set[str]],
    changed_files: set[str],
    expected_count: int,
) -> None:
    assert base.cluster_snapshot is not None
    assert incremental.cluster_snapshot is not None
    stable_symbols = {
        node.id for node in base.symbol_nodes() if node.file_path not in changed_files and node.id in incremental.nodes
    }
    assert len(stable_symbols) == expected_count
    for node_id in stable_symbols:
        assert _owner(base_clusters, node_id) == _owner(incremental_clusters, node_id)
        assert base.cluster_snapshot.node_paths[node_id][0] == incremental.cluster_snapshot.node_paths[node_id][0]


def _language_graphs(manifest_path: Path) -> list[RecordedGraphs]:
    manifest = json.loads(manifest_path.read_text())
    return [
        (
            ProgramGraph.from_dict(json.loads((manifest_path.parent / config["base_graph"]).read_text())),
            ProgramGraph.from_dict(json.loads((manifest_path.parent / config["incremental_graph"]).read_text())),
            ProgramGraph.from_dict(json.loads((manifest_path.parent / config["head_graph"]).read_text())),
            set(manifest["changed_files"]),
            config,
        )
        for config in manifest["languages"].values()
    ]


def _assert_incremental_scope(
    base: ProgramGraph,
    incremental: ProgramGraph,
    head: ProgramGraph,
    changed_files: set[str],
) -> None:
    assert set(incremental.nodes) == set(head.nodes)
    assert _edge_keys(incremental) == _edge_keys(head)
    outside_nodes = {
        node_id
        for node_id, node in base.nodes.items()
        if node.file_path and node.file_path not in changed_files and node_id in incremental.nodes
    }
    assert outside_nodes
    assert {node_id: incremental.nodes[node_id] for node_id in outside_nodes} == {
        node_id: base.nodes[node_id] for node_id in outside_nodes
    }

    def outside_edges(graph: ProgramGraph) -> set[tuple[str, str, str]]:
        return {
            (edge.kind.value, edge.source, edge.target)
            for edge in graph.edges
            if all(graph.nodes[node_id].file_path not in changed_files for node_id in (edge.source, edge.target))
        }

    assert outside_edges(base) <= outside_edges(incremental)


@pytest.mark.parametrize("manifest_path", _params())
def test_recorded_incremental_graph_matches_full_target(manifest_path: Path | None) -> None:
    assert manifest_path is not None
    for base, incremental, head, changed_files, config in _language_graphs(manifest_path):
        _assert_graph_changes(base, head, config["expected_graph_changes"])
        _assert_incremental_scope(base, incremental, head, changed_files)


@pytest.mark.parametrize("manifest_path", _params())
def test_recorded_incremental_clustering_preserves_scope(manifest_path: Path | None) -> None:
    assert manifest_path is not None
    for base, incremental, _head, changed_files, config in _language_graphs(manifest_path):
        clusterer = HierarchicalInfomapClusterer()
        base_result = clusterer.cluster(base)
        incremental.cluster_snapshot = base.cluster_snapshot
        incremental_result = clusterer.cluster(incremental)
        _assert_clustering(base_result, incremental_result, config["expected_clustering"])
        _assert_scope_stability(
            base,
            incremental,
            base_result.clusters,
            incremental_result.clusters,
            changed_files,
            config["expected_clustering"]["stable_outside_symbol_count"],
        )

        repeat_base = ProgramGraph.from_dict(base.to_dict())
        repeat_incremental = ProgramGraph.from_dict(incremental.to_dict())
        repeat_base_result = clusterer.cluster(repeat_base)
        repeat_incremental.cluster_snapshot = repeat_base.cluster_snapshot
        repeat_incremental_result = clusterer.cluster(repeat_incremental)
        assert repeat_base_result.clusters == base_result.clusters
        assert repeat_incremental_result.clusters == incremental_result.clusters
        assert repeat_incremental.cluster_snapshot is not None
        assert incremental.cluster_snapshot is not None
        assert repeat_incremental.cluster_snapshot.node_paths == incremental.cluster_snapshot.node_paths
