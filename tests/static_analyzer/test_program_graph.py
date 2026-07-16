from pathlib import Path
from math import log1p

import pytest

from static_analyzer.clustering import ClusterResult
from static_analyzer.constants import NodeType
from static_analyzer.infomap_clustering import HierarchicalInfomapClusterer
from static_analyzer.program_graph import (
    ProgramEdge,
    ProgramEdgeKind,
    ProgramGraph,
    ProgramNode,
    ProgramNodeKind,
    ProgramOccurrence,
    external_package_node_id,
    file_node_id,
    package_node_id,
    package_parent,
)


def _symbol(node_id: str, file_path: str) -> ProgramNode:
    return ProgramNode(
        node_id=node_id,
        kind=ProgramNodeKind.SYMBOL,
        language="python",
        name=node_id.rsplit(".", 1)[-1],
        file_path=file_path,
        symbol_type=NodeType.FUNCTION,
        line_start=1,
        line_end=3,
        reference_worthy=True,
    )


def _graph(tmp_path: Path) -> ProgramGraph:
    app = str(tmp_path / "app.py")
    helper = str(tmp_path / "helper.py")
    graph = ProgramGraph(language="python")
    for path in (app, helper):
        graph.add_node(ProgramNode(file_node_id(path), ProgramNodeKind.FILE, "python", path, path))
    graph.add_node(ProgramNode(package_node_id("python", "pkg"), ProgramNodeKind.PACKAGE, "python", "pkg"))
    graph.add_node(_symbol("pkg.app.run", app))
    graph.add_node(_symbol("pkg.helper.load", helper))
    graph.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, package_node_id("python", "pkg"), file_node_id(app)))
    graph.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, package_node_id("python", "pkg"), file_node_id(helper)))
    graph.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, file_node_id(app), "pkg.app.run"))
    graph.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, file_node_id(helper), "pkg.helper.load"))
    graph.add_edge(
        ProgramEdge(
            ProgramEdgeKind.CALL,
            "pkg.app.run",
            "pkg.helper.load",
            occurrences=[
                ProgramOccurrence(app, 2, 4),
                ProgramOccurrence(app, 3, 4),
            ],
        )
    )
    return graph


def test_call_projection_preserves_occurrences(tmp_path: Path) -> None:
    graph = _graph(tmp_path)

    call_graph = graph.to_call_graph()

    assert set(call_graph.nodes) == {"pkg.app.run", "pkg.helper.load"}
    assert len(call_graph.edges) == 1
    assert len(call_graph.edges[0].call_sites) == 2
    weighted = {
        (source, target): weight
        for source, target, weight in HierarchicalInfomapClusterer._weighted_edges(graph, set(graph.nodes))
    }
    assert weighted[("pkg.app.run", "pkg.helper.load")] == pytest.approx(log1p(2))


def test_external_packages_are_persisted_but_excluded_from_clustering(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    external_id = external_package_node_id("python", "requests")
    graph.add_node(ProgramNode(external_id, ProgramNodeKind.EXTERNAL_PACKAGE, "python", "requests"))
    graph.add_edge(ProgramEdge(ProgramEdgeKind.IMPORTS, file_node_id(str(tmp_path / "app.py")), external_id))

    assert external_id in graph.nodes
    weighted = HierarchicalInfomapClusterer._weighted_edges(graph, set(graph.nodes) - {external_id})
    assert all(external_id not in (source, target) for source, target, _weight in weighted)


def test_hierarchical_infomap_is_deterministic(tmp_path: Path) -> None:
    first = _graph(tmp_path)
    second = _graph(tmp_path)

    clusterer = HierarchicalInfomapClusterer()
    result_a = clusterer.cluster(first)
    result_b = clusterer.cluster(second)

    assert result_a.clusters == result_b.clusters
    assert set().union(*result_a.clusters.values()) == {"pkg.app.run", "pkg.helper.load"}
    assert first.cluster_snapshot is not None
    assert second.cluster_snapshot is not None
    assert first.cluster_snapshot.node_paths == second.cluster_snapshot.node_paths
    assert file_node_id(str(tmp_path / "app.py")) in first.cluster_snapshot.node_paths


def test_infomap_update_preserves_cluster_identity(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    clusterer = HierarchicalInfomapClusterer()
    before = clusterer.cluster(graph)
    app = str(tmp_path / "app.py")
    graph.add_node(_symbol("pkg.app.new_handler", app))
    graph.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, file_node_id(app), "pkg.app.new_handler"))
    graph.add_edge(ProgramEdge(ProgramEdgeKind.CALL, "pkg.app.new_handler", "pkg.app.run"))

    after = clusterer.cluster(graph)

    old_owner = next(cid for cid, members in before.clusters.items() if "pkg.app.run" in members)
    new_owner = next(cid for cid, members in after.clusters.items() if "pkg.app.run" in members)
    assert old_owner == new_owner


def test_symbol_scope_keeps_structural_context(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    HierarchicalInfomapClusterer().cluster(graph)

    scoped = graph.induced_by_symbols({"pkg.app.run"})

    assert set(scoped.nodes) == {
        package_node_id("python", "pkg"),
        file_node_id(str(tmp_path / "app.py")),
        "pkg.app.run",
    }
    assert scoped.cluster_snapshot is not graph.cluster_snapshot


def test_path_rewrite_updates_infomap_snapshot_node_ids(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    HierarchicalInfomapClusterer().cluster(graph)
    assert graph.cluster_snapshot is not None
    old_file_id = file_node_id(str(tmp_path / "app.py"))

    graph.visit_paths(lambda value: Path(value).name)

    new_file_id = file_node_id("app.py")
    assert new_file_id in graph.cluster_snapshot.node_paths
    assert old_file_id not in graph.cluster_snapshot.node_paths
    assert any(new_file_id in members for members in graph.cluster_snapshot.module_members.values())


def test_serialization_round_trip_preserves_typed_graph(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    graph.nodes["pkg.app.run"].metadata["visibility"] = "public"

    restored = ProgramGraph.from_dict(graph.to_dict())

    assert restored.to_dict() == graph.to_dict()
    assert restored.nodes["pkg.app.run"].id == "pkg.app.run"
    assert restored.nodes["pkg.app.run"].entity_label() == "Function"
    assert restored.nodes[package_node_id("python", "pkg")].entity_label() == "Package"


def test_hierarchy_and_package_dependencies_are_graph_projections(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    app_file = file_node_id(str(tmp_path / "app.py"))
    helper_file = file_node_id(str(tmp_path / "helper.py"))
    app_package = package_node_id("python", "app")
    helper_package = package_node_id("python", "helper")
    dependencies = ProgramGraph(language="python")
    dependencies.add_node(ProgramNode(app_file, ProgramNodeKind.FILE, "python", "app.py"))
    dependencies.add_node(ProgramNode(helper_file, ProgramNodeKind.FILE, "python", "helper.py"))
    dependencies.add_node(ProgramNode(app_package, ProgramNodeKind.PACKAGE, "python", "app"))
    dependencies.add_node(ProgramNode(helper_package, ProgramNodeKind.PACKAGE, "python", "helper"))
    dependencies.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, app_package, app_file))
    dependencies.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, helper_package, helper_file))
    dependencies.add_edge(ProgramEdge(ProgramEdgeKind.IMPORTS, app_file, helper_file))

    base = _symbol("pkg.Base", str(tmp_path / "helper.py"))
    base.symbol_type = NodeType.CLASS
    child = _symbol("pkg.Child", str(tmp_path / "app.py"))
    child.symbol_type = NodeType.CLASS
    graph.add_node(base)
    graph.add_node(child)
    graph.add_edge(ProgramEdge(ProgramEdgeKind.INHERITS, child.id, base.id))

    assert dependencies.package_dependencies()["app"]["imports"] == ["helper"]
    assert dependencies.package_dependencies()["helper"]["imported_by"] == ["app"]
    assert graph.hierarchy()[child.id]["superclasses"] == [base.id]
    assert graph.hierarchy()[base.id]["subclasses"] == [child.id]
    assert package_parent("app.services") == "app"
    assert package_parent("app") is None


def test_edge_merge_and_file_removal_are_deterministic(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    app = str(tmp_path / "app.py")
    edge = ProgramEdge(
        ProgramEdgeKind.CALL,
        "pkg.app.run",
        "pkg.helper.load",
        occurrences=[ProgramOccurrence(app, 4, 4), ProgramOccurrence(app, 2, 4)],
        metadata={"resolution": "lsp"},
    )

    graph.add_edge(edge)
    call_edge = graph.edges_of_kind(ProgramEdgeKind.CALL)[0]
    assert call_edge.occurrence_count == 3
    assert call_edge.metadata == {"resolution": "lsp"}

    remaining = graph.without_files({app})
    assert "pkg.app.run" not in remaining.nodes
    assert file_node_id(app) not in remaining.nodes
    assert remaining.edges_of_kind(ProgramEdgeKind.CALL) == []

    with pytest.raises(ValueError, match="endpoints must exist"):
        graph.add_edge(ProgramEdge(ProgramEdgeKind.CALL, "missing", "pkg.helper.load"))
    with pytest.raises(ValueError, match="Node ID collision"):
        graph.add_node(ProgramNode("pkg.app.run", ProgramNodeKind.FILE, "python", "collision"))
    with pytest.raises(ValueError, match="Cannot merge"):
        graph.merge(ProgramGraph(language="typescript"))


def test_cluster_result_path_rewrite_merges_equivalent_paths() -> None:
    result = ClusterResult(
        cluster_to_files={1: {"repo/a.py"}, 2: {"other/a.py"}},
        file_to_clusters={"repo/a.py": {1}, "other/a.py": {2}},
    )

    result.visit_paths(lambda path: Path(path).name)

    assert result.cluster_to_files == {1: {"a.py"}, 2: {"a.py"}}
    assert result.file_to_clusters == {"a.py": {1, 2}}
