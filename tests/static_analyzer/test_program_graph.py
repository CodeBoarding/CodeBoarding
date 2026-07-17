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


def _symbol(node_id: str, file_path: str, line_start: int = 1) -> ProgramNode:
    return ProgramNode(
        node_id=node_id,
        kind=ProgramNodeKind.SYMBOL,
        language="python",
        name=node_id.rsplit(".", 1)[-1],
        file_path=file_path,
        symbol_type=NodeType.FUNCTION,
        line_start=line_start,
        line_end=line_start + 2,
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


def test_call_edges_preserve_occurrences(tmp_path: Path) -> None:
    graph = _graph(tmp_path)

    assert graph.call_node_ids() == {"pkg.app.run", "pkg.helper.load"}
    assert len(graph.call_edges()) == 1
    assert graph.call_edges()[0].occurrence_count == 2
    weighted = {
        (source, target): weight
        for source, target, weight in HierarchicalInfomapClusterer._weighted_edges(graph, set(graph.nodes))
    }
    assert weighted[("pkg.app.run", "pkg.helper.load")] == pytest.approx(log1p(2))


def test_call_node_metric_excludes_reference_only_symbols(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    graph.add_node(
        ProgramNode(
            "pkg.app.setting",
            ProgramNodeKind.SYMBOL,
            "python",
            "setting",
            str(tmp_path / "app.py"),
            NodeType.VARIABLE,
            20,
            20,
            reference_worthy=True,
        )
    )

    assert graph.call_node_ids() == {"pkg.app.run", "pkg.helper.load"}
    graph.add_call("pkg.app.run", "pkg.app.setting")
    assert graph.call_node_ids() == {"pkg.app.run", "pkg.app.setting", "pkg.helper.load"}


def test_symbol_aliases_resolve_to_the_most_specific_name(tmp_path: Path) -> None:
    source_file = str(tmp_path / "source.py")
    target_file = str(tmp_path / "target.py")
    graph = ProgramGraph(language="python")
    graph.add_node(_symbol("source.run", source_file))
    graph.add_node(_symbol("target.handle", target_file))
    graph.add_call("source.run", "target.handle")
    graph.add_call("target.handle", "source.run")
    graph.add_node(_symbol("package.source.run", source_file))

    assert "source.run" not in graph.nodes
    assert graph.resolve_symbol_id("source.run") == "package.source.run"
    assert [(edge.source, edge.target) for edge in graph.call_edges()] == [
        ("package.source.run", "target.handle"),
        ("target.handle", "package.source.run"),
    ]
    restored = ProgramGraph.from_dict(graph.to_dict())
    assert restored.resolve_symbol_id("source.run") == "package.source.run"


def test_typed_edges_merge_metadata_and_reject_invalid_endpoints(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    source = file_node_id(str(tmp_path / "app.py"))
    target = file_node_id(str(tmp_path / "helper.py"))
    graph.add_edge(
        ProgramEdge(
            ProgramEdgeKind.IMPORTS,
            source,
            target,
            metadata={"declared_module": "pkg.helper"},
        )
    )
    graph.add_edge(
        ProgramEdge(
            ProgramEdgeKind.IMPORTS,
            source,
            target,
            metadata={"declared_modules": ["pkg.helper.api"], "resolved": True},
        )
    )

    imports = graph.edges_of_kind(ProgramEdgeKind.IMPORTS)[0]
    assert imports.metadata == {
        "declared_module": "pkg.helper",
        "declared_modules": ["pkg.helper", "pkg.helper.api"],
        "resolved": True,
    }
    with pytest.raises(ValueError, match="endpoints must exist"):
        graph.add_call("missing", "pkg.app.run")
    with pytest.raises(ValueError, match="different program edges"):
        imports.merge(ProgramEdge(ProgramEdgeKind.CALL, source, target))


def test_hierarchy_packages_filters_and_rendering(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    app = str(tmp_path / "app.py")
    helper = str(tmp_path / "helper.py")
    parent = ProgramNode(
        "pkg.app.Base",
        ProgramNodeKind.SYMBOL,
        "python",
        "Base",
        app,
        NodeType.CLASS,
        10,
        15,
    )
    child = ProgramNode(
        "pkg.helper.Child",
        ProgramNodeKind.SYMBOL,
        "python",
        "Child",
        helper,
        NodeType.CLASS,
        10,
        15,
    )
    graph.add_node(parent)
    graph.add_node(child)
    graph.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, file_node_id(app), parent.id))
    graph.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, file_node_id(helper), child.id))
    graph.add_edge(ProgramEdge(ProgramEdgeKind.INHERITS, child.id, parent.id))
    graph.add_edge(ProgramEdge(ProgramEdgeKind.IMPORTS, file_node_id(app), file_node_id(helper)))

    hierarchy = graph.hierarchy()
    assert hierarchy[parent.id]["subclasses"] == [child.id]
    assert hierarchy[child.id]["superclasses"] == [parent.id]
    assert graph.package_dependencies() == {"pkg": {"imports": [], "imported_by": []}}
    assert set(graph.filter_by_files({app}).symbols) == {"pkg.app.Base", "pkg.app.run"}
    assert graph.to_networkx().has_edge("pkg.app.run", "pkg.helper.load")

    clusters = ClusterResult(clusters={1: {"pkg.app.run"}, 2: {"pkg.helper.load"}}, strategy="test")
    rendered = graph.to_cluster_string(clusters)
    assert "pkg.app.run -> pkg.helper.load" in rendered
    assert "pkg.helper.load" not in graph.to_cluster_string(clusters, cluster_ids={1})
    graph.record_cluster_paths(clusters, "root")
    assert dict(graph.method_cluster_paths_snapshot())["pkg.app.run"] == {"root.1"}
    scoped = graph.filter_by_nodes({"pkg.app.run"})
    assert dict(scoped.method_cluster_paths_snapshot()) == {"pkg.app.run": {"root.1"}}
    without_app = graph.without_files({app})
    assert dict(without_app.method_cluster_paths_snapshot()) == {"pkg.helper.load": {"root.2"}}
    assert "pkg.app.run calls: pkg.helper.load" in graph.llm_str()
    assert graph.to_cluster_string(ClusterResult(strategy="empty")) == "empty"
    with pytest.raises(ValueError, match="Cannot merge"):
        graph.merge(ProgramGraph(language="go"))


def test_external_packages_are_persisted_but_excluded_from_clustering(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    external_id = external_package_node_id("python", "requests")
    graph.add_node(ProgramNode(external_id, ProgramNodeKind.EXTERNAL_PACKAGE, "python", "requests"))
    graph.add_edge(ProgramEdge(ProgramEdgeKind.IMPORTS, file_node_id(str(tmp_path / "app.py")), external_id))

    assert external_id in graph.nodes
    weighted = HierarchicalInfomapClusterer._weighted_edges(graph, set(graph.nodes) - {external_id})
    assert all(external_id not in (source, target) for source, target, _weight in weighted)
    assert external_id not in graph.without_files({str(tmp_path / "app.py")}).nodes


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
    before_owners = {node_id: cluster_id for cluster_id, members in before.clusters.items() for node_id in members}
    app = str(tmp_path / "app.py")
    graph.add_node(_symbol("pkg.app.new_handler", app, line_start=5))
    graph.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, file_node_id(app), "pkg.app.new_handler"))
    graph.add_edge(ProgramEdge(ProgramEdgeKind.CALL, "pkg.app.new_handler", "pkg.app.run"))

    after = clusterer.cluster(graph)

    after_owners = {node_id: cluster_id for cluster_id, members in after.clusters.items() for node_id in members}
    assert {node_id: after_owners[node_id] for node_id in before_owners} == before_owners
    assert after_owners["pkg.app.new_handler"] == before_owners["pkg.app.run"]


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
