import unittest

import networkx as nx

from static_analyzer.clustering import ClusterCache, ClusterResult
from static_analyzer.leiden_utils import find_partition
from static_analyzer.node import Node


class TestClusterCache(unittest.TestCase):
    def test_records_complete_root_lineage(self):
        cache = ClusterCache()
        result = ClusterResult(clusters={1: {"a.foo", "a.bar"}})

        cache.record_scope(result, {"a.orphan"})

        self.assertIs(cache.get_partition(), result)
        self.assertEqual(cache.get_unclustered_members(), {"a.orphan"})

    def test_record_scope_leaves_the_top_level_partition_alone(self):
        cache = ClusterCache()
        top_level = ClusterResult(clusters={1: {"a.foo"}})
        child = ClusterResult(clusters={2: {"a.foo"}})
        cache.record_scope(top_level)

        cache.record_scope(child, {"a.orphan"}, "1")

        self.assertIs(cache.get_partition(), top_level)
        self.assertIs(cache.get_partition("1"), child)
        self.assertEqual(cache.get_unclustered_members("1"), {"a.orphan"})

    def test_select_keeps_only_surviving_nodes(self):
        cache = ClusterCache()
        cache.record_scope(
            ClusterResult(
                clusters={1: {"a.foo", "b.bar"}},
                cluster_to_files={1: {"a.py", "b.py"}},
                file_to_clusters={"a.py": {1}, "b.py": {1}},
            ),
            {"a.orphan", "b.orphan"},
        )
        cache.record_scope(
            ClusterResult(clusters={2: {"a.foo", "b.bar"}}),
            {"a.foo", "b.orphan"},
            "1",
        )
        surviving = {"a.foo": Node("a.foo", 12, "a.py", 1, 5)}

        kept = cache.select(surviving)

        self.assertEqual(kept.get_partition().clusters, {1: {"a.foo"}})
        self.assertEqual(kept.get_partition().cluster_to_files, {1: {"a.py"}})
        self.assertEqual(kept.get_partition().file_to_clusters, {"a.py": {1}})
        self.assertEqual(kept.get_partition("1").clusters, {2: {"a.foo"}})
        self.assertEqual(kept.get_unclustered_members(), set())
        self.assertEqual(kept.get_unclustered_members("1"), {"a.foo"})

    def test_select_without_a_partition(self):
        """An unclustered language selects to an empty partition, never to None."""
        self.assertEqual(ClusterCache().select({}).get_partition().clusters, {})


class TestFindPartitionDeterminism(unittest.TestCase):
    """Property test: same input + same seed -> byte-equal output.

    Why: determinism is the contract every downstream piece relies on — cluster
    IDs persisted in analysis.json must reproduce on subsequent runs.
    """

    def test_find_partition_is_deterministic(self):
        graph = nx.karate_club_graph()

        a: list[set[int]] = find_partition(graph, seed=42)
        b: list[set[int]] = find_partition(graph, seed=42)

        self.assertEqual(sorted(sorted(c) for c in a), sorted(sorted(c) for c in b))
