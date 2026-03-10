import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agents.cluster_methods_mixin import ClusterMethodsMixin
from agents.agent_responses import AnalysisInsights, Component, SourceCodeReference
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


class TestEnsureUniqueFileAssignments(unittest.TestCase):
    """Test _ensure_unique_file_assignments deduplication."""

    def setUp(self):
        self.mixin = MockMixin(
            repo_dir=Path("/test/repo"),
            static_analysis=MagicMock(),
        )

    def _make_component(self, name: str, files: list[str]) -> Component:
        return Component(
            name=name,
            description=f"Description of {name}",
            key_entities=[],
            assigned_files=files,
            source_cluster_ids=[],
        )

    def _make_analysis(self, components: list[Component]) -> AnalysisInsights:
        return AnalysisInsights(
            description="Test analysis",
            components=components,
            components_relations=[],
        )

    def test_deduplicates_within_component(self):
        """Files are deduplicated within each component."""
        analysis = self._make_analysis(
            [
                self._make_component("A", ["a.py", "b.py", "a.py", "c.py", "b.py"]),
            ]
        )
        self.mixin._ensure_unique_file_assignments(analysis)
        self.assertEqual(analysis.components[0].assigned_files, ["a.py", "b.py", "c.py"])

    def test_preserves_files_across_components(self):
        """Same file in multiple components is preserved in all."""
        analysis = self._make_analysis(
            [
                self._make_component("A", ["shared.py", "a.py"]),
                self._make_component("B", ["shared.py", "b.py"]),
                self._make_component("Unclassified", ["shared.py", "c.py"]),
            ]
        )
        self.mixin._ensure_unique_file_assignments(analysis)
        self.assertEqual(analysis.components[0].assigned_files, ["shared.py", "a.py"])
        self.assertEqual(analysis.components[1].assigned_files, ["shared.py", "b.py"])
        self.assertEqual(analysis.components[2].assigned_files, ["shared.py", "c.py"])

    def test_complex_scenario(self):
        """Multiple components with varied deduplication needs."""
        analysis = self._make_analysis(
            [
                self._make_component("A", ["a.py", "a.py", "x.py"]),  # duplicate within
                self._make_component("B", ["x.py", "b.py", "b.py"]),  # x.py shared, duplicate within
                self._make_component("C", []),  # empty
                self._make_component("D", ["d.py"]),  # no duplicates
            ]
        )
        self.mixin._ensure_unique_file_assignments(analysis)
        self.assertEqual(analysis.components[0].assigned_files, ["a.py", "x.py"])
        self.assertEqual(analysis.components[1].assigned_files, ["x.py", "b.py"])
        self.assertEqual(analysis.components[2].assigned_files, [])
        self.assertEqual(analysis.components[3].assigned_files, ["d.py"])


if __name__ == "__main__":
    unittest.main()
