import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import (
    ClusterGroup,
    ClusterResult,
    ClusterScopeResult,
)
from static_analyzer.clustering.models import ClusterScopeInput
from static_analyzer.clustering.naming import ComponentVocabulary, NamingModel
from static_analyzer.clustering.service import ClusteringService
from static_analyzer.config import Language, NodeType
from static_analyzer.node import Node


def naming_model(components=(("Core", ("core",)),), machinery=()) -> NamingModel:
    """A minimal model, since clustering now requires one."""
    return NamingModel(
        components=tuple(ComponentVocabulary(name, tuple(owns)) for name, owns in components),
        machinery=frozenset(machinery),
    )


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

        # The model has to keep the two apart, or both leaves land in one component and
        # there is no pair of groups to compare induced graphs for.
        model = naming_model(
            (("A", tuple(sorted(members_a))), ("B", tuple(sorted(members_b)))),
        )
        result = ClusteringService(model)._cluster_hierarchy({"python": graph}, max_depth=2, scope_input=scope_input)

        group_a = next(group for group in result.groups if group.qualified_names == members_a)
        group_b = next(group for group in result.groups if group.qualified_names == members_b)
        self.assertEqual(seen_nodes[group_a.group_id], members_a)
        self.assertEqual(seen_nodes[group_b.group_id], members_b)
        self.assertEqual(seen_edges[group_a.group_id], {("a1", "a2")})
        self.assertEqual(seen_edges[group_b.group_id], set())
        self.assertNotIn(("a1", "b1"), seen_edges[group_a.group_id])
        self.assertNotIn(("a1", "b1"), seen_edges[group_b.group_id])
        self.assertTrue(group_a.expandable)
        self.assertFalse(group_b.expandable)
        self.assertIsNotNone(group_a.children)
        assert group_a.children is not None
        self.assertEqual(set(group_a.children.graphs_by_language["python"].nodes), members_a)
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
            return ClusterScopeInput(leaf_clusters_by_language={"python": partition})

        result = ClusteringService(naming_model())._cluster_hierarchy(
            {"python": graph}, max_depth=3, scope_input=scope_input
        )

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
            return ClusterScopeInput(leaf_clusters_by_language={"python": partition})

        result = ClusteringService(naming_model())._cluster_hierarchy(
            {"python": graph}, max_depth=3, scope_input=scope_input
        )

        self.assertFalse(result.groups[0].expandable)
        self.assertIsNone(result.groups[0].children)

    def test_oversized_scope_expands_despite_a_single_anchored_group(self):
        graph = graph_for([f"n{index:03d}" for index in range(121)], one_file=True)

        def scope_input(scope_id: str, graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
            current = graphs["python"]
            if scope_id == "root":
                return ClusterScopeInput(
                    leaf_clusters_by_language={"python": partition_for(current, {1: set(current.nodes)})}
                )
            partition = split_partition(current)
            return ClusterScopeInput(
                leaf_clusters_by_language={"python": partition},
                previous_owner={cluster_id: "1.1" for cluster_id in partition.clusters},
            )

        with patch("static_analyzer.clustering.service.scope_is_separable") as separable:
            result = ClusteringService(naming_model())._cluster_hierarchy(
                {"python": graph},
                max_depth=2,
                scope_input=scope_input,
            )

        self.assertTrue(result.groups[0].expandable)
        self.assertIsNotNone(result.groups[0].children)
        separable.assert_not_called()

    def test_smaller_scope_counts_unanchored_groups(self):
        graph = graph_for([f"n{index:02d}" for index in range(31)], one_file=True)

        def scope_input(scope_id: str, graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
            current = graphs["python"]
            if scope_id == "root":
                return ClusterScopeInput(
                    leaf_clusters_by_language={"python": partition_for(current, {1: set(current.nodes)})}
                )
            partition = split_partition(current)
            return ClusterScopeInput(
                leaf_clusters_by_language={"python": partition},
                previous_owner={cluster_id: "1.1" for cluster_id in partition.clusters},
            )

        with patch("static_analyzer.clustering.service.scope_is_separable", return_value=True):
            result = ClusteringService(naming_model())._cluster_hierarchy(
                {"python": graph},
                max_depth=2,
                scope_input=scope_input,
            )

        child = result.groups[0].children
        self.assertIsNotNone(child)
        assert child is not None
        self.assertEqual(len(child.groups), 1)
        self.assertGreaterEqual(child.unanchored_group_count, 2)

    def test_expansion_gate_receives_the_child_graph_callable_count(self):
        graph = graph_for([f"n{index:02d}" for index in range(31)], one_file=True)
        graph.add_node(Node("Container", NodeType.CLASS, "/repo/scope.py", 100, 110))

        def scope_input(scope_id: str, graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
            current = graphs["python"]
            partition = (
                partition_for(current, {1: set(current.nodes)}) if scope_id == "root" else split_partition(current)
            )
            return ClusterScopeInput(leaf_clusters_by_language={"python": partition})

        with patch("static_analyzer.clustering.service.scope_is_separable", return_value=False) as separable:
            ClusteringService(naming_model())._cluster_hierarchy(
                {"python": graph}, max_depth=2, scope_input=scope_input
            )

        self.assertEqual(separable.call_args.kwargs["method_count"], 31)

    def test_real_separability_gate_expands_a_modular_fresh_scope(self):
        names = [f"n{index:02d}" for index in range(31)]
        communities = [names[index::5] for index in range(5)]
        edges = [
            edge
            for left, right in ((communities[0], communities[1]), (communities[2], communities[3]))
            for edge in [(source, target) for source in left for target in right]
            + [(source, target) for source in right for target in left]
        ]
        edges.extend((source, target) for source in communities[4] for target in communities[3])
        graph = graph_for(names, one_file=True, edges=edges)

        def scope_input(scope_id: str, graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
            current = graphs["python"]
            partition = (
                partition_for(current, {1: set(current.nodes)}) if scope_id == "root" else split_partition(current)
            )
            return ClusterScopeInput(leaf_clusters_by_language={"python": partition})

        result = ClusteringService(naming_model())._cluster_hierarchy(
            {"python": graph}, max_depth=2, scope_input=scope_input
        )

        self.assertTrue(result.groups[0].expandable)
        self.assertIsNotNone(result.groups[0].children)

    def test_retains_a_persisted_child_scope_when_it_is_no_longer_separable(self):
        graph = graph_for([f"n{index:02d}" for index in range(31)], one_file=True)

        def scope_input(scope_id: str, graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
            current = graphs["python"]
            partition = (
                partition_for(current, {1: set(current.nodes)}) if scope_id == "root" else split_partition(current)
            )
            return ClusterScopeInput(
                leaf_clusters_by_language={"python": partition},
                previous_owner={cluster_id: "1.1" for cluster_id in partition.clusters},
                retain_scope=scope_id != "root",
            )

        with patch("static_analyzer.clustering.service.scope_is_separable", return_value=False):
            result = ClusteringService(naming_model())._cluster_hierarchy(
                {"python": graph}, max_depth=2, scope_input=scope_input
            )

        self.assertTrue(result.groups[0].expandable)
        self.assertIsNotNone(result.groups[0].children)

    def test_rejects_a_depth_below_the_root_level(self):
        with self.assertRaisesRegex(ValueError, "max_depth must be at least 1"):
            ClusteringService(naming_model())._cluster_hierarchy({}, max_depth=0)

    @patch.object(ClusteringService, "_build_leaf_clusters")
    @patch.object(ClusteringService, "_cluster_hierarchy")
    def test_orchestration_records_structural_and_unclustered_lineage(
        self,
        cluster_hierarchy,
        build_root,
    ):
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
                    symbol_members_by_language={"python": {"grandchild", "grandchild.orphan"}},
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
                    symbol_members_by_language={"python": {"child", "child.orphan"}},
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
                    symbol_members_by_language={"python": {"root", "root.orphan"}},
                    children=child_scope,
                )
            ],
        )
        static_analysis = MagicMock()
        static_analysis.available_cfgs.return_value = {"python": CallGraph(language="python")}
        cache = MagicMock()
        static_analysis.get_clusters.return_value = cache
        build_root.return_value = {"python": root_partition}
        cluster_hierarchy.return_value = hierarchy

        result = ClusteringService(naming_model()).build_full_hierarchy(static_analysis, max_depth=3)

        self.assertIs(result, hierarchy)
        graphs, depth, scope_input = cluster_hierarchy.call_args.args
        self.assertEqual(graphs, static_analysis.available_cfgs.return_value)
        self.assertEqual(depth, 3)
        self.assertIs(scope_input("root", graphs).leaf_clusters_by_language["python"], root_partition)
        self.assertEqual(scope_input("1", graphs).leaf_clusters_by_language, {})
        self.assertEqual(
            cache.record_scope.call_args_list,
            [
                call(root_partition, {"root.orphan"}, ""),
                call(child_partition, {"child.orphan"}, "1"),
                call(grandchild_partition, {"grandchild.orphan"}, "1.1"),
            ],
        )
        static_analysis.get_clusters.assert_called_with(Language.PYTHON)

    @patch.object(ClusteringService, "_cluster_hierarchy")
    def test_selected_scope_records_structural_lineage(self, cluster_hierarchy):
        partition = ClusterResult(clusters={4: {"child"}})
        hierarchy = ClusterScopeResult(
            scope_id="2",
            leaf_clusters_by_language={"python": partition},
            groups=[
                ClusterGroup(
                    group_id="2.1",
                    cluster_ids=[4],
                    symbol_members_by_language={"python": {"child", "orphan"}},
                )
            ],
        )
        graph = CallGraph(language="python")
        static_analysis = MagicMock()
        cache = MagicMock()
        static_analysis.get_clusters.return_value = cache
        cluster_hierarchy.return_value = hierarchy

        result = ClusteringService(naming_model()).build_scope_hierarchy(
            static_analysis,
            {"python": graph},
            max_depth=2,
            root_scope_id="2",
        )

        self.assertIs(result, hierarchy)
        cache.record_scope.assert_called_once_with(partition, {"orphan"}, "2")

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

        hierarchy = ClusteringService(naming_model()).build_incremental_hierarchy(
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
