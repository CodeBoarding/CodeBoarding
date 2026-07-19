"""Infomap baseline snapshot tests."""

from diagram_analysis.cluster_snapshot import snapshot_from_cluster_results, snapshot_from_static_analysis
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering import ClusterResult, InfomapClusterSnapshot
from static_analyzer.constants import Language, NodeType
from static_analyzer.program_graph import ProgramGraph, ProgramNode, ProgramNodeKind


def _graph(node_specs: list[tuple[str, str]]) -> ProgramGraph:
    graph = ProgramGraph(language="python")
    for index, (qualified_name, file_path) in enumerate(node_specs, start=1):
        graph.add_node(
            ProgramNode(
                node_id=qualified_name,
                kind=ProgramNodeKind.SYMBOL,
                language="python",
                name=qualified_name.rsplit(".", 1)[-1],
                file_path=file_path,
                symbol_type=NodeType.FUNCTION,
                line_start=index,
                line_end=index,
                reference_worthy=True,
            )
        )
    return graph


def _persist_partition(graph: ProgramGraph, clusters: dict[int, set[str]]) -> None:
    cluster_to_files = {
        cluster_id: {graph.nodes[node_id].file_path for node_id in members if node_id in graph.nodes}
        for cluster_id, members in clusters.items()
    }
    file_to_clusters: dict[str, set[int]] = {}
    for cluster_id, files in cluster_to_files.items():
        for file_path in files:
            file_to_clusters.setdefault(file_path, set()).add(cluster_id)
    graph.cluster_snapshot = InfomapClusterSnapshot(
        cluster_result=ClusterResult(
            clusters=clusters,
            cluster_to_files=cluster_to_files,
            file_to_clusters=file_to_clusters,
            strategy="hierarchical_infomap",
        ),
        node_paths={member: (cluster_id,) for cluster_id, members in clusters.items() for member in members},
        module_members={cluster_id: set(members) for cluster_id, members in clusters.items()},
        next_cluster_id=max(clusters, default=0) + 1,
    )


def _static(graph: ProgramGraph) -> StaticAnalysisResults:
    results = StaticAnalysisResults()
    results.add_program_graph(Language.PYTHON, graph)
    return results


def test_snapshot_reads_persisted_infomap_partition() -> None:
    graph = _graph([("a.foo", "a.py"), ("a.bar", "a.py"), ("b.baz", "b.py")])
    _persist_partition(graph, {5: {"a.foo", "a.bar"}, 6: {"b.baz"}})

    snapshot = snapshot_from_static_analysis(_static(graph))

    assert snapshot.by_language["python"][5].members == {"a.foo", "a.bar"}
    assert snapshot.by_language["python"][5].files == {"a.py"}
    assert snapshot.by_language["python"][5].member_files == {"a.foo": "a.py", "a.bar": "a.py"}


def test_graph_without_persisted_lineage_is_not_a_valid_baseline() -> None:
    snapshot = snapshot_from_static_analysis(_static(_graph([("a.foo", "a.py")])))

    assert snapshot.all_cluster_ids() == set()


def test_stale_snapshot_members_are_removed_by_infomap_refresh() -> None:
    graph = _graph([("a.foo", "a.py")])
    _persist_partition(graph, {1: {"a.foo", "ghost.fn"}})

    snapshot = snapshot_from_static_analysis(_static(graph))

    assert snapshot.by_language["python"][1].members == {"a.foo"}
    assert snapshot.by_language["python"][1].member_files == {"a.foo": "a.py"}


def test_snapshot_from_cluster_results_copies_members_and_files() -> None:
    snapshot = snapshot_from_cluster_results(
        {
            "python": ClusterResult(
                clusters={1: {"a.foo", "a.bar"}},
                cluster_to_files={1: {"a.py"}},
                file_to_clusters={"a.py": {1}},
            )
        }
    )

    assert snapshot.by_language["python"][1].members == {"a.foo", "a.bar"}
    assert snapshot.by_language["python"][1].files == {"a.py"}
