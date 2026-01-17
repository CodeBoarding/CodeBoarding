import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agents.cluster_methods_mixin import ClusterMethodsMixin
from agents.agent_responses import Component, SourceCodeReference
from static_analyzer.graph import ClusterResult


class MockMixin(ClusterMethodsMixin):
    """Concrete implementation for testing the mixin."""

    def __init__(self, repo_dir: Path, static_analysis: MagicMock):
        self.repo_dir = repo_dir
        self.static_analysis = static_analysis


class TestClusterResult(unittest.TestCase):
    """Test the ClusterResult dataclass from graph.py"""

    def test_get_cluster_ids(self):
        result = ClusterResult(
            clusters={1: {"a", "b"}, 2: {"c"}, 3: {"d", "e", "f"}},
            file_to_clusters={},
            cluster_to_files={},
            strategy="test",
        )
        self.assertEqual(result.get_cluster_ids(), {1, 2, 3})

    def test_get_files_for_cluster(self):
        result = ClusterResult(
            clusters={1: {"a"}},
            file_to_clusters={},
            cluster_to_files={1: {"/test/a.py", "/test/b.py"}, 2: {"/test/c.py"}},
            strategy="test",
        )
        self.assertEqual(result.get_files_for_cluster(1), {"/test/a.py", "/test/b.py"})
        self.assertEqual(result.get_files_for_cluster(2), {"/test/c.py"})
        self.assertEqual(result.get_files_for_cluster(99), set())

    def test_get_clusters_for_file(self):
        result = ClusterResult(
            clusters={1: {"a"}},
            file_to_clusters={"/test/a.py": {1, 2}, "/test/b.py": {3}},
            cluster_to_files={},
            strategy="test",
        )
        self.assertEqual(result.get_clusters_for_file("/test/a.py"), {1, 2})
        self.assertEqual(result.get_clusters_for_file("/test/b.py"), {3})
        self.assertEqual(result.get_clusters_for_file("/nonexistent.py"), set())

    def test_get_nodes_for_cluster(self):
        result = ClusterResult(
            clusters={1: {"node_a", "node_b"}, 2: {"node_c"}},
            file_to_clusters={},
            cluster_to_files={},
            strategy="test",
        )
        self.assertEqual(result.get_nodes_for_cluster(1), {"node_a", "node_b"})
        self.assertEqual(result.get_nodes_for_cluster(99), set())


if __name__ == "__main__":
    unittest.main()
