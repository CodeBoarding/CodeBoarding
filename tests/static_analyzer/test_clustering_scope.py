import unittest

from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import ClusterResult, ClusteringService, METHOD_LEVEL_STRATEGY
from static_analyzer.cluster_helpers import supercluster_leaf_ids
from static_analyzer.constants import NodeType
from static_analyzer.node import Node


def graph_for(language: str, names: list[str], edges: list[tuple[str, str]] = ()) -> CallGraph:
    graph = CallGraph(language=language)
    for index, name in enumerate(names):
        graph.add_node(Node(name, NodeType.FUNCTION, f"/repo/{language}/{name}.py", index + 1, index + 2))
    for source, target in edges:
        graph.add_edge(source, target, call_sites=[{"file": f"{language}.py", "line": 10}])
    return graph


def partition_for(graph: CallGraph, clusters: dict[int, set[str]]) -> ClusterResult:
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
        strategy="test",
    )


class TestClusteringScope(unittest.TestCase):
    def test_grouping_matches_the_existing_supercluster_result(self):
        graph = graph_for("python", ["a", "b", "c"], [("a", "b"), ("b", "c")])
        partition = partition_for(graph, {1: {"a"}, 2: {"b"}, 3: {"c"}})

        expected, expected_modularity = supercluster_leaf_ids(
            {"python": partition}, {"python": graph.to_networkx(reference_kinds=())}
        )
        result = ClusteringService().cluster_scope({"python": graph}, partitions={"python": partition})

        self.assertEqual(
            sorted(sorted(group.cluster_ids) for group in result.groups),
            sorted(sorted(group) for group in expected),
        )
        self.assertEqual(result.modularity, expected_modularity)

    def test_members_are_a_complete_disjoint_cover_including_orphans(self):
        graph = graph_for("python", ["a", "b", "orphan"], [("a", "orphan"), ("orphan", "b")])
        partition = partition_for(graph, {1: {"a"}, 2: {"b"}})

        result = ClusteringService().cluster_scope({"python": graph}, partitions={"python": partition})

        assigned = [name for group in result.groups for name in group.members.get("python", set())]
        self.assertEqual(set(assigned), set(graph.nodes))
        self.assertEqual(len(assigned), len(set(assigned)))

    def test_connections_retain_direction_and_concrete_call_evidence(self):
        graph = graph_for("python", ["caller", "callee"], [("caller", "callee")])
        partition = partition_for(graph, {1: {"caller"}, 2: {"callee"}})

        result = ClusteringService().cluster_scope({"python": graph}, partitions={"python": partition})

        self.assertEqual(len(result.connections), 1)
        connection = result.connections[0]
        self.assertNotEqual(connection.source_group_id, connection.target_group_id)
        self.assertEqual(len(connection.edges), 1)
        self.assertEqual(connection.edges[0].source, "caller")
        self.assertEqual(connection.edges[0].target, "callee")
        self.assertEqual(connection.edges[0].call_sites, [{"file": "python.py", "line": 10}])

    def test_language_partitions_receive_disjoint_cluster_ids(self):
        python = graph_for("python", ["py.a"])
        typescript = graph_for("typescript", ["ts.a"])

        result = ClusteringService().cluster_scope(
            {"python": python, "typescript": typescript},
            partitions={
                "python": partition_for(python, {1: {"py.a"}}),
                "typescript": partition_for(typescript, {1: {"ts.a"}}),
            },
        )

        python_ids = set(result.partitions["python"].clusters)
        typescript_ids = set(result.partitions["typescript"].clusters)
        self.assertTrue(python_ids.isdisjoint(typescript_ids))

    def test_previous_owners_become_stable_group_ids(self):
        graph = graph_for("python", ["a", "b", "c"])
        partition = partition_for(graph, {1: {"a"}, 2: {"b"}, 3: {"c"}})
        _groups, expected_fresh_modularity = supercluster_leaf_ids(
            {"python": partition}, {"python": graph.to_networkx(reference_kinds=())}
        )

        result = ClusteringService().cluster_scope(
            {"python": graph},
            partitions={"python": partition},
            previous_owner={1: "2", 2: "4", 3: "7"},
        )

        self.assertEqual([group.group_id for group in result.groups], ["2", "4", "7"])
        self.assertEqual(result.fresh_modularity, expected_fresh_modularity)
        self.assertFalse(result.regrouped)

    def test_method_level_fallback_is_available_for_child_scopes(self):
        graph = graph_for("python", ["a", "b", "c", "d", "e"])
        partition = partition_for(graph, {1: set(graph.nodes)})

        result = ClusteringService().cluster_scope(
            {"python": graph},
            partitions={"python": partition},
            method_level_fallback=True,
        )

        expanded = result.partitions["python"]
        self.assertEqual(expanded.strategy, METHOD_LEVEL_STRATEGY)
        self.assertEqual(len(expanded.clusters), 5)
        self.assertTrue(all(len(members) == 1 for members in expanded.clusters.values()))


if __name__ == "__main__":
    unittest.main()
