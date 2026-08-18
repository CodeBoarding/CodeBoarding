import unittest
from collections.abc import Mapping

from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import ClusterResult, ClusterScopeInput, ClusteringService
from static_analyzer.constants import NodeType
from static_analyzer.node import Node


def graph_for(names: list[str], *, one_file: bool = False, edges: list[tuple[str, str]] = ()) -> CallGraph:
    graph = CallGraph(language="python")
    for index, name in enumerate(names):
        file_name = "scope.py" if one_file else f"{name}.py"
        graph.add_node(Node(name, NodeType.FUNCTION, f"/repo/{file_name}", index + 1, index + 2))
    for source, target in edges:
        graph.add_edge(source, target, call_sites=[{"file": "scope.py", "line": 10}])
    return graph


def partition_for(graph: CallGraph, clusters: dict[int, set[str]], strategy: str = "test") -> ClusterResult:
    cluster_to_files = {
        cluster_id: {graph.nodes[name].file_path for name in members} for cluster_id, members in clusters.items()
    }
    file_to_clusters: dict[str, set[int]] = {}
    for cluster_id, files in cluster_to_files.items():
        for file_path in files:
            file_to_clusters.setdefault(file_path, set()).add(cluster_id)
    return ClusterResult(
        clusters=clusters,
        cluster_to_files=cluster_to_files,
        file_to_clusters=file_to_clusters,
        strategy=strategy,
    )


def split_partition(graph: CallGraph, count: int = 5) -> ClusterResult:
    names = sorted(graph.nodes)
    clusters = {index + 1: set(names[index::count]) for index in range(count)}
    return partition_for(graph, {cluster_id: members for cluster_id, members in clusters.items() if members})


class TestClusteringHierarchy(unittest.TestCase):
    def test_each_child_uses_its_exact_induced_graph(self):
        members_a = {f"a{index}" for index in range(13)}
        members_b = {"b1", "b2"}
        graph = graph_for(sorted(members_a | members_b), edges=[("a1", "b1")])
        seen_nodes: dict[str, set[str]] = {}
        seen_edges: dict[str, set[tuple[str, str]]] = {}

        def scope_input(scope_id: str, graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
            current = graphs["python"]
            seen_nodes[scope_id] = set(current.nodes)
            seen_edges[scope_id] = {(edge.get_source(), edge.get_destination()) for edge in current.edges}
            if scope_id == "root":
                partition = partition_for(current, {1: members_a, 2: members_b})
            else:
                partition = split_partition(current)
            return ClusterScopeInput(partitions={"python": partition})

        result = ClusteringService().cluster_hierarchy({"python": graph}, max_depth=2, scope_input=scope_input)

        group_a = next(group for group in result.groups if group.qualified_names == members_a)
        group_b = next(group for group in result.groups if group.qualified_names == members_b)
        self.assertEqual(seen_nodes[group_a.group_id], members_a)
        self.assertEqual(seen_nodes[group_b.group_id], members_b)
        self.assertEqual(seen_edges[group_a.group_id], set())
        self.assertEqual(seen_edges[group_b.group_id], set())
        self.assertIsNotNone(group_a.children)
        self.assertIsNone(group_b.children)

    def test_recurses_with_new_local_partitions_until_the_depth_cap(self):
        graph = graph_for([f"n{index:02d}" for index in range(65)])
        seen_nodes: dict[str, set[str]] = {}

        def scope_input(scope_id: str, graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
            current = graphs["python"]
            seen_nodes[scope_id] = set(current.nodes)
            partition = (
                partition_for(current, {1: set(current.nodes)}) if scope_id == "root" else split_partition(current)
            )
            return ClusterScopeInput(partitions={"python": partition})

        result = ClusteringService().cluster_hierarchy({"python": graph}, max_depth=3, scope_input=scope_input)

        root_group = result.groups[0]
        self.assertIsNotNone(root_group.children)
        assert root_group.children is not None
        self.assertTrue(root_group.children.groups)
        for child_group in root_group.children.groups:
            self.assertEqual(seen_nodes[child_group.group_id], child_group.qualified_names)
            self.assertIsNotNone(child_group.children)
            assert child_group.children is not None
            self.assertTrue(all(grandchild.children is None for grandchild in child_group.children.groups))

    def test_small_scope_stops_at_the_existing_size_gate(self):
        graph = graph_for([f"n{index}" for index in range(10)], one_file=True)

        def scope_input(scope_id: str, graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
            current = graphs["python"]
            partition = (
                partition_for(current, {1: set(current.nodes)}) if scope_id == "root" else split_partition(current)
            )
            return ClusterScopeInput(partitions={"python": partition})

        result = ClusteringService().cluster_hierarchy({"python": graph}, max_depth=3, scope_input=scope_input)

        self.assertIsNone(result.groups[0].children)

    def test_rejects_a_depth_below_the_root_level(self):
        with self.assertRaisesRegex(ValueError, "max_depth must be at least 1"):
            ClusteringService().cluster_hierarchy({}, max_depth=0)


if __name__ == "__main__":
    unittest.main()
