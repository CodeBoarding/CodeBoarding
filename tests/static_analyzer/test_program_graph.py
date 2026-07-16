from pathlib import Path
from math import log1p

import pytest

from static_analyzer.constants import NodeType
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
    weighted = graph.clustering_graph()
    assert weighted["pkg.app.run"]["pkg.helper.load"]["weight"] == pytest.approx(log1p(2))


def test_external_packages_are_persisted_but_excluded_from_clustering(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    external_id = external_package_node_id("python", "requests")
    graph.add_node(ProgramNode(external_id, ProgramNodeKind.EXTERNAL_PACKAGE, "python", "requests"))
    graph.add_edge(ProgramEdge(ProgramEdgeKind.IMPORTS, file_node_id(str(tmp_path / "app.py")), external_id))

    assert external_id in graph.nodes
    assert external_id not in graph.clustering_graph()


def test_hierarchical_infomap_is_deterministic(tmp_path: Path) -> None:
    first = _graph(tmp_path)
    second = _graph(tmp_path)

    result_a = first.cluster()
    result_b = second.cluster()

    assert result_a.clusters == result_b.clusters
    assert set().union(*result_a.clusters.values()) == {"pkg.app.run", "pkg.helper.load"}
    assert first._cluster_snapshot is not None
    assert second._cluster_snapshot is not None
    assert first._cluster_snapshot.node_paths == second._cluster_snapshot.node_paths
    assert file_node_id(str(tmp_path / "app.py")) in first._cluster_snapshot.node_paths


def test_infomap_update_preserves_cluster_identity(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    before = graph.cluster()
    app = str(tmp_path / "app.py")
    graph.add_node(_symbol("pkg.app.new_handler", app))
    graph.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, file_node_id(app), "pkg.app.new_handler"))
    graph.add_edge(ProgramEdge(ProgramEdgeKind.CALL, "pkg.app.new_handler", "pkg.app.run"))

    after = graph.cluster()

    old_owner = next(cid for cid, members in before.clusters.items() if "pkg.app.run" in members)
    new_owner = next(cid for cid, members in after.clusters.items() if "pkg.app.run" in members)
    assert old_owner == new_owner


def test_symbol_scope_keeps_structural_context(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    graph.cluster()

    scoped = graph.induced_by_symbols({"pkg.app.run"})

    assert set(scoped.nodes) == {
        package_node_id("python", "pkg"),
        file_node_id(str(tmp_path / "app.py")),
        "pkg.app.run",
    }
    assert scoped._cluster_snapshot is not graph._cluster_snapshot


def test_path_rewrite_updates_infomap_snapshot_node_ids(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    graph.cluster()
    assert graph._cluster_snapshot is not None
    old_file_id = file_node_id(str(tmp_path / "app.py"))

    graph.visit_paths(lambda value: Path(value).name)

    new_file_id = file_node_id("app.py")
    assert new_file_id in graph._cluster_snapshot.node_paths
    assert old_file_id not in graph._cluster_snapshot.node_paths
    assert any(new_file_id in members for members in graph._cluster_snapshot.module_members.values())
