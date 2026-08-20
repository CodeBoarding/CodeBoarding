import unittest

from static_analyzer.cfg import CallGraph
from static_analyzer.config import NodeType
from static_analyzer.clustering import (
    METHOD_LEVEL_STRATEGY,
    ClusterResult,
    ClusteringService,
    LeafClustersUnavailableError,
)
from static_analyzer.clustering.grouping import GroupingService
from static_analyzer.node import Node


def graph_for(language: str, names: list[str], edges: list[tuple[str, str]] = ()) -> CallGraph:
    graph = CallGraph(language=language)
    for index, name in enumerate(names):
        graph.add_node(Node(name, NodeType.FUNCTION, f"/repo/{language}/{name}.py", index + 1, index + 2))
    for source, target in edges:
        graph.add_edge(source, target, call_sites=[{"file": f"{language}.py", "line": 10}])
    return graph


def cluster_result_for(graph: CallGraph, clusters: dict[int, set[str]]) -> ClusterResult:
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
        cluster_result = cluster_result_for(graph, {1: {"a"}, 2: {"b"}, 3: {"c"}})

        expected, expected_modularity = GroupingService().group(
            {"python": cluster_result}, {"python": graph.to_networkx(reference_kinds=())}
        )
        result = ClusteringService().cluster_scope(
            {"python": graph}, leaf_clusters_by_language={"python": cluster_result}
        )

        self.assertEqual(
            sorted(sorted(group.cluster_ids) for group in result.groups),
            sorted(sorted(group) for group in expected),
        )
        self.assertEqual(result.modularity, expected_modularity)

    def test_symbol_members_are_a_complete_disjoint_cover_including_orphans(self):
        graph = graph_for("python", ["a", "b", "orphan"], [("a", "orphan"), ("orphan", "b")])
        cluster_result = cluster_result_for(graph, {1: {"a"}, 2: {"b"}})

        result = ClusteringService().cluster_scope(
            {"python": graph}, leaf_clusters_by_language={"python": cluster_result}
        )

        assigned = [name for group in result.groups for name in group.symbol_members_by_language.get("python", set())]
        self.assertEqual(set(assigned), set(graph.nodes))
        self.assertEqual(len(assigned), len(set(assigned)))

    def test_connections_retain_direction_and_concrete_call_evidence(self):
        graph = graph_for("python", ["caller", "callee"], [("caller", "callee")])
        cluster_result = cluster_result_for(graph, {1: {"caller"}, 2: {"callee"}})

        result = ClusteringService().cluster_scope(
            {"python": graph}, leaf_clusters_by_language={"python": cluster_result}
        )

        self.assertEqual(len(result.connections), 1)
        connection = result.connections[0]
        self.assertNotEqual(connection.source_group_id, connection.target_group_id)
        self.assertEqual(len(connection.edges), 1)
        self.assertEqual(connection.edges[0].source_qualified_name, "caller")
        self.assertEqual(connection.edges[0].target_qualified_name, "callee")
        self.assertEqual(connection.edges[0].call_sites, [{"file": "python.py", "line": 10}])

    def test_language_leaf_clusters_receive_disjoint_cluster_ids(self):
        python = graph_for("python", ["py.a"])
        typescript = graph_for("typescript", ["ts.a"])

        result = ClusteringService().cluster_scope(
            {"python": python, "typescript": typescript},
            leaf_clusters_by_language={
                "python": cluster_result_for(python, {1: {"py.a"}}),
                "typescript": cluster_result_for(typescript, {1: {"ts.a"}}),
            },
        )

        python_ids = set(result.leaf_clusters_by_language["python"].clusters)
        typescript_ids = set(result.leaf_clusters_by_language["typescript"].clusters)
        self.assertTrue(python_ids.isdisjoint(typescript_ids))

    def test_previous_owners_become_stable_group_ids(self):
        graph = graph_for("python", ["a", "b", "c"])
        cluster_result = cluster_result_for(graph, {1: {"a"}, 2: {"b"}, 3: {"c"}})
        _groups, expected_unanchored_modularity = GroupingService().group(
            {"python": cluster_result},
            {"python": graph.to_networkx(reference_kinds=())},
        )

        result = ClusteringService().cluster_scope(
            {"python": graph},
            leaf_clusters_by_language={"python": cluster_result},
            previous_owner={1: "2", 2: "4", 3: "7"},
        )

        self.assertEqual([group.group_id for group in result.groups], ["2", "4", "7"])
        self.assertEqual(result.unanchored_modularity, expected_unanchored_modularity)
        self.assertFalse(result.regrouped)

    def test_new_group_ids_follow_the_highest_surviving_sibling(self):
        group_ids = ClusteringService._allocate_group_ids("root", ["2", ""])

        self.assertEqual(group_ids, ["2", "3"])

    def test_default_clustering_raises_when_nonempty_scope_has_no_leaf_clusters(self):
        graph = graph_for("python", ["a", "b"])

        with self.assertRaisesRegex(LeafClustersUnavailableError, "python"):
            ClusteringService().cluster_scope({"python": graph})

    def test_empty_leaf_clusters_for_a_nonempty_language_raise(self):
        python = graph_for("python", ["py.a"])
        typescript = graph_for("typescript", ["ts.a"])

        with self.assertRaisesRegex(LeafClustersUnavailableError, "typescript"):
            ClusteringService().cluster_scope(
                {"python": python, "typescript": typescript},
                leaf_clusters_by_language={
                    "python": cluster_result_for(python, {1: {"py.a"}}),
                    "typescript": ClusterResult(),
                },
            )

    def test_method_level_fallback_is_available_for_child_scopes(self):
        graph = graph_for("python", ["a", "b", "c", "d", "e"])
        cluster_result = cluster_result_for(graph, {1: set(graph.nodes)})

        result = ClusteringService().cluster_scope(
            {"python": graph},
            leaf_clusters_by_language={"python": cluster_result},
            method_level_fallback=True,
        )

        expanded = result.leaf_clusters_by_language["python"]
        self.assertEqual(expanded.strategy, METHOD_LEVEL_STRATEGY)
        self.assertEqual(len(expanded.clusters), 5)
        self.assertTrue(all(len(members) == 1 for members in expanded.clusters.values()))

    def test_method_level_fallback_preserves_owners_by_member(self):
        graph = graph_for("python", ["a", "b", "c", "d", "e", "new"])
        cluster_result = cluster_result_for(
            graph,
            {
                0: {"d", "e"},
                4: {"a", "b", "c"},
            },
        )

        result = ClusteringService().cluster_scope(
            {"python": graph},
            scope_id="9",
            leaf_clusters_by_language={"python": cluster_result},
            previous_owner={0: "9.1", 4: "9.2"},
            method_level_fallback=True,
        )

        owner_by_member = {
            member: group.previous_component_id
            for group in result.groups
            for member in group.symbol_members_by_language.get("python", set())
        }
        self.assertTrue(all(owner_by_member[member] == "9.1" for member in {"d", "e"}))
        self.assertTrue(all(owner_by_member[member] == "9.2" for member in {"a", "b", "c"}))


if __name__ == "__main__":
    unittest.main()
