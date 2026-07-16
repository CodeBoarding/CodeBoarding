"""Relational clustering assertions over recorded two-commit ProgramGraphs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from static_analyzer.program_graph import ProgramGraph


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "cfg_cluster_commit_pairs"
MANIFESTS = sorted(FIXTURE_ROOT.glob("*/manifest.json"))


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
    assert set(expected.get("added_nodes", [])) <= added_nodes
    assert set(expected.get("removed_nodes", [])) <= removed_nodes
    assert {tuple(item) for item in expected.get("added_edges", [])} <= added_edges
    assert {tuple(item) for item in expected.get("removed_edges", [])} <= removed_edges


def _assert_clustering(base_result, head_result, expected: dict) -> None:
    base_clusters = base_result.clusters
    head_clusters = head_result.clusters
    for item in expected.get("joins_existing", []):
        assert _owner(head_clusters, item["node"]) == _owner(head_clusters, item["anchor"])
    for item in expected.get("stable", []):
        assert _owner(base_clusters, item) == _owner(head_clusters, item)
    for item in expected.get("moves", []):
        assert _owner(base_clusters, item["node"]) == _owner(base_clusters, item["old_anchor"])
        assert _owner(head_clusters, item["node"]) == _owner(head_clusters, item["new_anchor"])
    for item in expected.get("new_clusters", []):
        owners = {_owner(head_clusters, node_id) for node_id in item["nodes"]}
        assert len(owners) == 1
        assert all(next(iter(owners)) != _owner(head_clusters, anchor) for anchor in item.get("distinct_from", []))
    for item in expected.get("merges", []):
        assert len({_owner(head_clusters, node_id) for node_id in item}) == 1
    for item in expected.get("splits", []):
        assert len({_owner(base_clusters, node_id) for group in item for node_id in group}) == 1
        split_owners = [{_owner(head_clusters, node_id) for node_id in group} for group in item]
        assert all(len(group_owners) == 1 for group_owners in split_owners)
        assert len({next(iter(group_owners)) for group_owners in split_owners}) == len(split_owners)
    for node_id in expected.get("absent", []):
        assert all(node_id not in members for members in head_clusters.values())


@pytest.mark.parametrize("manifest_path", _params())
def test_recorded_cfg_clustering_change(manifest_path: Path | None) -> None:
    assert manifest_path is not None
    manifest = json.loads(manifest_path.read_text())
    for language, config in manifest["languages"].items():
        base = ProgramGraph.from_dict(json.loads((manifest_path.parent / config["base_graph"]).read_text()))
        head = ProgramGraph.from_dict(json.loads((manifest_path.parent / config["head_graph"]).read_text()))
        _assert_graph_changes(base, head, config.get("expected_graph_changes", {}))
        base_result = base.cluster()
        head._cluster_snapshot = base._cluster_snapshot
        head_result = head.cluster()
        _assert_clustering(base_result, head_result, config.get("expected_clustering", {}))
