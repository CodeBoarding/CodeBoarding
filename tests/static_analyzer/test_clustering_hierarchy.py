import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import MagicMock, patch

from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import (
    ClusterCache,
    ClusterGroup,
    ClusterResult,
    ClusterScopeResult,
    record_cluster_hierarchy,
)
from static_analyzer.clustering.models import ClusterScopeInput
from static_analyzer.clustering.service import ClusteringService
from static_analyzer.config import NodeType
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
        graph = graph_for(sorted(members_a | members_b), edges=[("a1", "a2"), ("a1", "b1")])
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
            return ClusterScopeInput(leaf_clusters_by_language={"python": partition})

        result = ClusteringService()._cluster_hierarchy({"python": graph}, max_depth=2, scope_input=scope_input)

        group_a = next(group for group in result.groups if group.qualified_names == members_a)
        group_b = next(group for group in result.groups if group.qualified_names == members_b)
        self.assertEqual(seen_nodes[group_a.group_id], members_a)
        self.assertEqual(seen_nodes[group_b.group_id], members_b)
        self.assertEqual(seen_edges[group_a.group_id], {("a1", "a2")})
        self.assertEqual(seen_edges[group_b.group_id], set())
        self.assertNotIn(("a1", "b1"), seen_edges[group_a.group_id])
        self.assertNotIn(("a1", "b1"), seen_edges[group_b.group_id])
        self.assertTrue(group_a.expandable)
        self.assertTrue(group_b.expandable)
        self.assertIsNotNone(group_a.children)
        assert group_a.children is not None
        self.assertEqual(set(group_a.children.graphs_by_language["python"].nodes), members_a)
        self.assertIsNotNone(group_b.children)

    def test_recurses_with_new_local_partitions_until_the_depth_cap(self):
        graph = graph_for([f"n{index:02d}" for index in range(65)])
        seen_nodes: dict[str, set[str]] = {}

        def scope_input(scope_id: str, graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
            current = graphs["python"]
            seen_nodes[scope_id] = set(current.nodes)
            partition = (
                partition_for(current, {1: set(current.nodes)}) if scope_id == "root" else split_partition(current)
            )
            return ClusterScopeInput(leaf_clusters_by_language={"python": partition})

        result = ClusteringService()._cluster_hierarchy({"python": graph}, max_depth=3, scope_input=scope_input)

        root_group = result.groups[0]
        self.assertIsNotNone(root_group.children)
        assert root_group.children is not None
        self.assertTrue(root_group.children.groups)
        for child_group in root_group.children.groups:
            self.assertEqual(seen_nodes[child_group.group_id], child_group.qualified_names)
            self.assertIsNotNone(child_group.children)
            assert child_group.children is not None
            self.assertTrue(all(grandchild.children is None for grandchild in child_group.children.groups))

    def test_stops_when_leiden_does_not_split_the_scope(self):
        graph = graph_for([f"n{index}" for index in range(10)], one_file=True)

        def scope_input(_scope_id: str, graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
            current = graphs["python"]
            return ClusterScopeInput(
                leaf_clusters_by_language={"python": partition_for(current, {1: set(current.nodes)})}
            )

        result = ClusteringService()._cluster_hierarchy({"python": graph}, max_depth=3, scope_input=scope_input)

        self.assertFalse(result.groups[0].expandable)
        self.assertIsNone(result.groups[0].children)

    def test_retains_a_persisted_child_scope_when_leiden_does_not_split_it(self):
        graph = graph_for([f"n{index:02d}" for index in range(31)], one_file=True)

        def scope_input(scope_id: str, graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
            current = graphs["python"]
            partition = partition_for(current, {1: set(current.nodes)})
            return ClusterScopeInput(
                leaf_clusters_by_language={"python": partition},
                retain_scope=scope_id != "root",
            )

        result = ClusteringService()._cluster_hierarchy({"python": graph}, max_depth=2, scope_input=scope_input)

        self.assertTrue(result.groups[0].expandable)
        self.assertIsNotNone(result.groups[0].children)

    def test_depth_cap_does_not_run_hidden_child_clustering(self):
        graph = graph_for(["a", "b"], edges=[("a", "b")])

        with patch(
            "static_analyzer.clustering.service.cluster_graph",
            return_value=partition_for(graph, {1: set(graph.nodes)}),
        ) as cluster:
            result = ClusteringService().build_hierarchy({"python": graph}, max_depth=1)

        cluster.assert_called_once()
        self.assertTrue(result.groups[0].expandable)
        self.assertIsNone(result.groups[0].children)

    def test_build_hierarchy_runs_leiden_on_each_induced_community_until_no_split(self):
        members_a = {"a1", "a2", "a3"}
        members_b = {"b1", "b2"}
        all_members = members_a | members_b
        graph = graph_for(sorted(all_members), edges=[("a1", "a2"), ("a1", "b1")])
        seen_scopes: list[set[str]] = []

        def cluster(scoped_graph) -> ClusterResult:
            members = set(scoped_graph.nodes)
            seen_scopes.append(members)
            communities = {1: members_a, 2: members_b} if members == all_members else {1: members}
            return ClusterResult(clusters=communities, strategy="leiden")

        with patch("static_analyzer.clustering.service.cluster_graph", side_effect=cluster):
            result = ClusteringService().build_hierarchy({"python": graph}, max_depth=3)

        self.assertEqual(seen_scopes, [all_members, members_a, members_b])
        self.assertEqual(
            {frozenset(group.qualified_names) for group in result.groups}, {frozenset(members_a), frozenset(members_b)}
        )
        self.assertTrue(all(group.children is None for group in result.groups))

    def test_selected_scope_uses_the_same_fresh_algorithm_as_the_repository_root(self):
        names = [f"a{index}" for index in range(5)] + [f"b{index}" for index in range(5)]
        edges = [
            (source, target)
            for prefix in ("a", "b")
            for source in [name for name in names if name.startswith(prefix)]
            for target in [name for name in names if name.startswith(prefix)]
            if source != target
        ]
        graph = graph_for(names, edges=edges)

        root = ClusteringService().build_hierarchy({"python": graph}, max_depth=1)
        selected = ClusteringService().build_hierarchy(
            {"python": graph},
            max_depth=1,
            root_scope_id="7",
        )

        self.assertEqual(
            {frozenset(group.qualified_names) for group in root.groups},
            {frozenset(group.qualified_names) for group in selected.groups},
        )
        self.assertTrue(all(group.group_id.startswith("7.") for group in selected.groups))

    def test_rejects_a_depth_below_the_root_level(self):
        with self.assertRaisesRegex(ValueError, "max_depth must be at least 1"):
            ClusteringService()._cluster_hierarchy({}, max_depth=0)

    def test_record_hierarchy_records_each_structural_partition(self):
        root_partition = ClusterResult(clusters={1: {"root"}})
        child_partition = ClusterResult(clusters={2: {"child"}})
        grandchild_partition = ClusterResult(clusters={3: {"grandchild"}})
        grandchild_scope = ClusterScopeResult(
            scope_id="1.1",
            leaf_clusters_by_language={"python": grandchild_partition},
            groups=[
                ClusterGroup(
                    group_id="1.1.1",
                    cluster_ids=[3],
                    symbol_members_by_language={"python": {"grandchild"}},
                )
            ],
        )
        child_scope = ClusterScopeResult(
            scope_id="1",
            leaf_clusters_by_language={"python": child_partition},
            groups=[
                ClusterGroup(
                    group_id="1.1",
                    cluster_ids=[2],
                    symbol_members_by_language={"python": {"child"}},
                    children=grandchild_scope,
                )
            ],
        )
        hierarchy = ClusterScopeResult(
            scope_id="root",
            leaf_clusters_by_language={"python": root_partition},
            groups=[
                ClusterGroup(
                    group_id="1",
                    cluster_ids=[1],
                    symbol_members_by_language={"python": {"root"}},
                    children=child_scope,
                )
            ],
        )
        cache = ClusterCache()

        record_cluster_hierarchy({"python": cache}, hierarchy)

        self.assertIs(cache.get_partition(), root_partition)
        self.assertEqual(cache.get_unclustered_members(), set())
        self.assertIs(cache.get_partition("1"), child_partition)
        self.assertEqual(cache.get_unclustered_members("1"), set())
        self.assertIs(cache.get_partition("1.1"), grandchild_partition)
        self.assertEqual(cache.get_unclustered_members("1.1"), set())

    def test_record_hierarchy_uses_selected_scope_prefix(self):
        partition = ClusterResult(clusters={4: {"child"}})
        hierarchy = ClusterScopeResult(
            scope_id="2",
            leaf_clusters_by_language={"python": partition},
            groups=[
                ClusterGroup(
                    group_id="2.1",
                    cluster_ids=[4],
                    symbol_members_by_language={"python": {"child"}},
                )
            ],
        )
        cache = ClusterCache()

        record_cluster_hierarchy({"python": cache}, hierarchy)

        self.assertIs(cache.get_partition("2"), partition)
        self.assertEqual(cache.get_unclustered_members("2"), set())

    def test_incremental_hierarchy_preserves_surviving_member_ownership(self):
        graph = graph_for(["pkg.changed", "stable.one", "stable.two"], one_file=True)
        root_partition = partition_for(graph, {1: set(graph.nodes)})
        persisted_components = [
            MagicMock(
                component_id="1",
                source_cluster_ids=["1"],
                file_methods=[
                    MagicMock(
                        file_path="scope.py",
                        methods=[
                            MagicMock(qualified_name="pkg.changed"),
                            MagicMock(qualified_name="pkg.removed"),
                        ],
                    )
                ],
            ),
            MagicMock(
                component_id="2",
                source_cluster_ids=["2"],
                file_methods=[
                    MagicMock(
                        file_path="scope.py",
                        methods=[
                            MagicMock(qualified_name="stable.one"),
                            MagicMock(qualified_name="stable.two"),
                        ],
                    )
                ],
            ),
        ]
        persisted = MagicMock(components=persisted_components)
        static_analysis = MagicMock()
        static_analysis.incremental_base_results = MagicMock()
        static_analysis.available_cfgs.return_value = {"python": graph}

        hierarchy = ClusteringService().build_incremental_hierarchy(
            static_analysis,
            max_depth=1,
            root_leaf_clusters={"python": root_partition},
            persisted_scopes={"root": persisted},
            repo_dir=Path("/repo"),
            artifact_dir=Path("/artifacts"),
        )

        self.assertEqual(
            {group.group_id: group.qualified_names for group in hierarchy.groups},
            {
                "1": {"pkg.changed"},
                "2": {"stable.one", "stable.two"},
            },
        )


if __name__ == "__main__":
    unittest.main()
