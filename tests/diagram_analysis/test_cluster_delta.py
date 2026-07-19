"""ProgramGraph-aware incremental impact tests."""

import copy

from pathlib import Path

from agents.content_hash import hash_method_body
from agents.file_index_models import FileEntry, MethodEntry
from diagram_analysis.cluster_delta import compute_cluster_delta, structural_diff_from_delta
from diagram_analysis.cluster_snapshot import snapshot_from_static_analysis
from repo_utils.change_detector import ChangeSet
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering import ClusterResult, InfomapClusterSnapshot
from static_analyzer.constants import Language, NodeType
from static_analyzer.program_graph import (
    ProgramEdge,
    ProgramEdgeKind,
    ProgramGraph,
    ProgramNode,
    ProgramNodeKind,
    ProgramOccurrence,
    file_node_id,
)


def _symbol(qualified_name: str, file_path: str, start: int = 1, end: int = 2) -> ProgramNode:
    return ProgramNode(
        node_id=qualified_name,
        kind=ProgramNodeKind.SYMBOL,
        language="python",
        name=qualified_name.rsplit(".", 1)[-1],
        file_path=file_path,
        symbol_type=NodeType.FUNCTION,
        line_start=start,
        line_end=end,
        reference_worthy=True,
    )


def _graph(files: dict[str, list[str]]) -> ProgramGraph:
    graph = ProgramGraph(language="python")
    for file_path, symbols in files.items():
        file_id = file_node_id(file_path)
        graph.add_node(ProgramNode(file_id, ProgramNodeKind.FILE, "python", file_path, file_path))
        for index, qualified_name in enumerate(symbols, start=1):
            node = _symbol(qualified_name, file_path, index * 3, index * 3 + 1)
            graph.add_node(node)
            graph.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, file_id, node.id))
    return graph


def _persist_partition(
    graph: ProgramGraph,
    clusters: dict[int, set[str]],
    file_owners: dict[str, int] | None = None,
) -> None:
    owner = {member: cluster_id for cluster_id, members in clusters.items() for member in members}
    for file_path, cluster_id in (file_owners or {}).items():
        owner[file_node_id(file_path)] = cluster_id
    module_members: dict[int, set[str]] = {cluster_id: set(members) for cluster_id, members in clusters.items()}
    for node_id, cluster_id in owner.items():
        module_members.setdefault(cluster_id, set()).add(node_id)
    cluster_to_files = {
        cluster_id: {graph.nodes[member].file_path for member in members if member in graph.nodes}
        for cluster_id, members in clusters.items()
    }
    file_to_clusters: dict[str, set[int]] = {}
    for cluster_id, paths in cluster_to_files.items():
        for path in paths:
            file_to_clusters.setdefault(path, set()).add(cluster_id)
    graph.cluster_snapshot = InfomapClusterSnapshot(
        cluster_result=ClusterResult(
            clusters={cluster_id: set(members) for cluster_id, members in clusters.items()},
            cluster_to_files=cluster_to_files,
            file_to_clusters=file_to_clusters,
            strategy="hierarchical_infomap",
        ),
        node_paths={node_id: (cluster_id,) for node_id, cluster_id in owner.items()},
        module_members=module_members,
        next_cluster_id=max(clusters, default=0) + 1,
    )


def _static(graph: ProgramGraph) -> StaticAnalysisResults:
    results = StaticAnalysisResults()
    results.add_program_graph(Language.PYTHON, graph)
    return results


def _multi_static(python_graph: ProgramGraph, typescript_graph: ProgramGraph) -> StaticAnalysisResults:
    results = StaticAnalysisResults()
    results.add_program_graph(Language.PYTHON, python_graph)
    results.add_program_graph(Language.TYPESCRIPT, typescript_graph)
    return results


def _baseline_and_current(files: dict[str, list[str]], clusters: dict[int, set[str]]):
    baseline_graph = _graph(files)
    _persist_partition(
        baseline_graph,
        clusters,
        {
            file_path: min(cluster_id for cluster_id, members in clusters.items() if set(symbols) & members)
            for file_path, symbols in files.items()
        },
    )
    baseline = _static(baseline_graph)
    snapshot = snapshot_from_static_analysis(baseline)
    current_graph = ProgramGraph.from_dict(baseline_graph.to_dict())
    return baseline, snapshot, current_graph


def test_unchanged_infomap_partition_has_no_delta() -> None:
    baseline, snapshot, current_graph = _baseline_and_current(
        {"a.py": ["a.run"], "b.py": ["b.load"]},
        {1: {"a.run"}, 2: {"b.load"}},
    )

    delta = compute_cluster_delta(snapshot, _static(current_graph), old_static=baseline)

    assert not delta.has_changes


def test_legacy_cross_language_snapshot_adopts_persisted_global_ids() -> None:
    baseline_python = _graph({"a.py": ["a.run"]})
    baseline_typescript = _graph({"t.ts": ["t.run"]})
    baseline_typescript.language = "typescript"
    _persist_partition(baseline_python, {1: {"a.run"}}, {"a.py": 1})
    _persist_partition(baseline_typescript, {1: {"t.run"}}, {"t.ts": 1})
    current_python = copy.deepcopy(baseline_python)
    current_typescript = copy.deepcopy(baseline_typescript)
    baseline = _multi_static(baseline_python, baseline_typescript)
    current = _multi_static(current_python, current_typescript)

    new_file_id = file_node_id("b.py")
    current_python.add_node(ProgramNode(new_file_id, ProgramNodeKind.FILE, "python", "b.py", "b.py"))
    current_python.add_node(_symbol("b.run", "b.py", 1, 2))
    current_python.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, new_file_id, "b.run"))

    snapshot = snapshot_from_static_analysis(baseline)
    delta = compute_cluster_delta(
        snapshot,
        current,
        changes=ChangeSet.from_changed_files(["b.py"], [], []),
        old_static=baseline,
    )

    assert set(delta.by_language["python"].cluster_results.clusters) == {1, 3}
    assert set(delta.by_language["typescript"].cluster_results.clusters) == {2}


def test_new_symbol_joins_existing_cluster_without_moving_stable_symbols() -> None:
    baseline, snapshot, current_graph = _baseline_and_current({"a.py": ["a.run"]}, {1: {"a.run"}})
    current_graph.add_node(_symbol("a.handle", "a.py", 7, 8))
    current_graph.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, file_node_id("a.py"), "a.handle"))
    current_graph.add_edge(ProgramEdge(ProgramEdgeKind.CALL, "a.handle", "a.run"))

    delta = compute_cluster_delta(
        snapshot,
        _static(current_graph),
        changes=ChangeSet.from_changed_files(["a.py"], [], []),
        old_static=baseline,
    )

    assert delta.by_language["python"].changed_cluster_ids == {1}
    assert delta.by_language["python"].cluster_results.clusters[1] == {"a.run", "a.handle"}


def test_method_hash_marks_only_owning_cluster(tmp_path: Path) -> None:
    source = tmp_path / "a.py"
    source.write_text("def run():\n    return 2\n")
    baseline, snapshot, current_graph = _baseline_and_current({str(source): ["a.run"]}, {1: {"a.run"}})
    current_graph.nodes["a.run"].line_start = 1
    current_graph.nodes["a.run"].line_end = 2
    previous_files = {
        "a.py": FileEntry(
            methods=[
                MethodEntry(
                    qualified_name="a.run",
                    start_line=1,
                    end_line=2,
                    node_type="FUNCTION",
                    content_hash=hash_method_body(["def run():", "    return 1"], 1, 2),
                )
            ]
        )
    }

    delta = compute_cluster_delta(
        snapshot,
        _static(current_graph),
        changes=ChangeSet.from_changed_files([], ["a.py"], []),
        repo_dir=tmp_path,
        old_static=baseline,
        previous_files=previous_files,
    )
    structural = structural_diff_from_delta(
        snapshot,
        delta,
        changes=ChangeSet.from_changed_files([], ["a.py"], []),
        repo_dir=tmp_path,
    )

    assert delta.by_language["python"].modified_methods_by_cluster == {1: {"a.run"}}
    assert structural.by_language["python"].modified[0].modified_methods == {"a.run"}


def test_file_change_without_method_or_graph_change_freezes_components() -> None:
    baseline, snapshot, current_graph = _baseline_and_current({"a.py": ["a.run"]}, {1: {"a.run"}})

    delta = compute_cluster_delta(
        snapshot,
        _static(current_graph),
        changes=ChangeSet.from_changed_files([], ["a.py"], []),
        old_static=baseline,
    )

    assert not delta.has_changes


def test_import_change_impacts_importer_cluster_not_target_cluster() -> None:
    baseline, snapshot, current_graph = _baseline_and_current(
        {"a.py": ["a.run"], "b.py": ["b.load"]},
        {1: {"a.run"}, 2: {"b.load"}},
    )
    current_graph.add_edge(ProgramEdge(ProgramEdgeKind.IMPORTS, file_node_id("a.py"), file_node_id("b.py")))

    delta = compute_cluster_delta(
        snapshot,
        _static(current_graph),
        changes=ChangeSet.from_changed_files([], ["a.py"], []),
        old_static=baseline,
    )

    changes = delta.by_language["python"].edge_changes_by_cluster
    assert set(changes) == {1}
    assert changes[1][0].kind == ProgramEdgeKind.IMPORTS
    assert changes[1][0].related_cluster_ids == (2,)


def test_call_site_line_drift_does_not_modify_architectural_edge() -> None:
    baseline_graph = _graph({"a.py": ["a.run"], "b.py": ["b.load"]})
    baseline_graph.add_edge(ProgramEdge(ProgramEdgeKind.CALL, "a.run", "b.load", [ProgramOccurrence("a.py", 10, 3)]))
    _persist_partition(baseline_graph, {1: {"a.run"}, 2: {"b.load"}}, {"a.py": 1, "b.py": 2})
    baseline = _static(baseline_graph)
    snapshot = snapshot_from_static_analysis(baseline)
    current_graph = _graph({"a.py": ["a.run"], "b.py": ["b.load"]})
    current_graph.add_edge(ProgramEdge(ProgramEdgeKind.CALL, "a.run", "b.load", [ProgramOccurrence("a.py", 40, 3)]))
    _persist_partition(current_graph, {1: {"a.run"}, 2: {"b.load"}}, {"a.py": 1, "b.py": 2})

    delta = compute_cluster_delta(
        snapshot,
        _static(current_graph),
        changes=ChangeSet.from_changed_files([], ["a.py"], []),
        old_static=baseline,
    )

    assert not delta.has_changes
