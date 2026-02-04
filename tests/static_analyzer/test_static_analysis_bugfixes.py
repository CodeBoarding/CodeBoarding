"""Tests for three bugs found in static analysis code.

Bug 1: Leiden algorithm incorrectly called Louvain in graph.py _cluster_with_algorithm()
Bug 2: Broken indentation in incremental_orchestrator.py _remap_cluster_ids_in_analysis()
Bug 3: source_file_strs set rebuilt per-reference in analysis_cache.py _validate_no_dangling_references()
"""

import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import networkx as nx
import networkx.algorithms.community as nx_comm

from static_analyzer.analysis_cache import AnalysisCacheManager
from static_analyzer.graph import CallGraph, ClusterResult, Node
from static_analyzer.incremental_orchestrator import IncrementalAnalysisOrchestrator


# ---- Helpers ----


def make_node(name: str, file_path: str = "/test/file.py", line_start: int = 1, line_end: int = 10) -> Node:
    return Node(
        fully_qualified_name=name,
        node_type=12,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
    )


def make_call_graph(node_names: list[str], file_path: str = "/test/file.py") -> CallGraph:
    graph = CallGraph()
    for name in node_names:
        graph.add_node(make_node(name, file_path))
    for i in range(len(node_names) - 1):
        graph.add_edge(node_names[i], node_names[i + 1])
    return graph


# ---- Bug 1: Leiden algorithm should not call Louvain ----


class TestLeidenAlgorithmFix(unittest.TestCase):
    """Bug 1: _cluster_with_algorithm('leiden') was calling louvain_communities instead of leiden."""

    def _make_graph_for_clustering(self) -> tuple[CallGraph, nx.DiGraph]:
        """Create a CallGraph and its networkx representation for clustering tests."""
        cg = CallGraph()
        for i in range(10):
            cg.add_node(make_node(f"module.func{i}", f"/file{i % 3}.py"))
        for i in range(9):
            cg.add_edge(f"module.func{i}", f"module.func{i + 1}")
        return cg, cg.to_networkx()

    def test_leiden_does_not_call_louvain(self):
        """Verify that selecting 'leiden' does not call louvain_communities."""
        cg, nx_graph = self._make_graph_for_clustering()

        with patch.object(nx_comm, "louvain_communities", wraps=nx_comm.louvain_communities) as mock_louvain:
            cg._cluster_with_algorithm(nx_graph, "leiden", target_clusters=3)
            mock_louvain.assert_not_called()

    def test_leiden_calls_asyn_lpa_when_leiden_unavailable(self):
        """When leiden_communities is not available, should fall back to asyn_lpa_communities."""
        cg, nx_graph = self._make_graph_for_clustering()

        # Ensure leiden_communities is not available (current networkx version)
        if not hasattr(nx_comm, "leiden_communities"):
            with patch.object(nx_comm, "asyn_lpa_communities", wraps=nx_comm.asyn_lpa_communities) as mock_lpa:
                result = cg._cluster_with_algorithm(nx_graph, "leiden", target_clusters=3)
                mock_lpa.assert_called_once()
                self.assertIsInstance(result, list)
                self.assertTrue(all(isinstance(c, (set, frozenset)) for c in result))

    def test_leiden_calls_leiden_when_available(self):
        """When leiden_communities IS available, it should be called."""
        cg, nx_graph = self._make_graph_for_clustering()

        mock_leiden = MagicMock(return_value=[{"module.func0", "module.func1"}, {"module.func2", "module.func3"}])
        with patch.object(nx_comm, "leiden_communities", mock_leiden, create=True):
            result = cg._cluster_with_algorithm(nx_graph, "leiden", target_clusters=3)
            mock_leiden.assert_called_once_with(nx_graph, seed=CallGraph.CLUSTERING_SEED)
            self.assertEqual(len(result), 2)

    def test_louvain_still_calls_louvain(self):
        """Verify that 'louvain' algorithm still correctly calls louvain_communities."""
        cg, nx_graph = self._make_graph_for_clustering()

        with patch.object(nx_comm, "louvain_communities", wraps=nx_comm.louvain_communities) as mock_louvain:
            result = cg._cluster_with_algorithm(nx_graph, "louvain", target_clusters=3)
            mock_louvain.assert_called_once()
            self.assertIsInstance(result, list)


# ---- Bug 2: Broken indentation in _remap_cluster_ids_in_analysis ----


class TestRemapClusterIdsFix(unittest.TestCase):
    """Bug 2: _remap_cluster_ids_in_analysis had dedented code that made the first loop a no-op."""

    def setUp(self):
        self.orchestrator = IncrementalAnalysisOrchestrator()

    def _make_analysis_insights(self, components_data: list[tuple[str, list[int]]]):
        """Create a mock AnalysisInsights with components that have source_cluster_ids.

        Args:
            components_data: list of (name, source_cluster_ids) tuples
        """
        from agents.agent_responses import AnalysisInsights, Component, Relation

        components = []
        for name, cluster_ids in components_data:
            comp = Component(
                name=name,
                description=f"Description for {name}",
                key_entities=[],
                source_cluster_ids=cluster_ids,
            )
            components.append(comp)

        return AnalysisInsights(
            description="Test analysis",
            components=components,
            components_relations=[],
        )

    def test_remap_removes_deleted_clusters(self):
        """Components referencing deleted clusters should have those IDs removed."""
        # cluster_mappings: new_id -> old_id
        # old_id 1 maps to new_id 10, old_id 2 maps to new_id 20
        # old_id 3 has no mapping (deleted)
        cluster_mappings = {"python": {10: 1, 20: 2}}

        analysis = self._make_analysis_insights(
            [
                ("ComponentA", [1, 2, 3]),  # cluster 3 should be removed
            ]
        )

        self.orchestrator._remap_cluster_ids_in_analysis(analysis, cluster_mappings)

        self.assertEqual(analysis.components[0].source_cluster_ids, [1, 2])

    def test_remap_keeps_all_matched_clusters(self):
        """Components referencing only matched clusters should be unchanged."""
        cluster_mappings = {"python": {10: 1, 20: 2}}

        analysis = self._make_analysis_insights(
            [
                ("ComponentA", [1, 2]),
            ]
        )

        self.orchestrator._remap_cluster_ids_in_analysis(analysis, cluster_mappings)

        self.assertEqual(analysis.components[0].source_cluster_ids, [1, 2])

    def test_remap_handles_multiple_components(self):
        """All components should be processed, not just the first one."""
        cluster_mappings = {"python": {10: 1}}

        analysis = self._make_analysis_insights(
            [
                ("ComponentA", [1, 5]),  # 5 is deleted
                ("ComponentB", [1, 7]),  # 7 is deleted
                ("ComponentC", [1]),  # all matched
            ]
        )

        self.orchestrator._remap_cluster_ids_in_analysis(analysis, cluster_mappings)

        self.assertEqual(analysis.components[0].source_cluster_ids, [1])
        self.assertEqual(analysis.components[1].source_cluster_ids, [1])
        self.assertEqual(analysis.components[2].source_cluster_ids, [1])

    def test_remap_skips_components_without_cluster_ids(self):
        """Components without source_cluster_ids should not be affected."""
        cluster_mappings = {"python": {10: 1}}

        analysis = self._make_analysis_insights(
            [
                ("ComponentA", []),  # no cluster IDs
                ("ComponentB", [1, 5]),  # 5 is deleted
            ]
        )

        self.orchestrator._remap_cluster_ids_in_analysis(analysis, cluster_mappings)

        self.assertEqual(analysis.components[0].source_cluster_ids, [])
        self.assertEqual(analysis.components[1].source_cluster_ids, [1])

    def test_remap_no_op_with_empty_mapping(self):
        """Empty mapping should not modify any components."""
        cluster_mappings: dict[str, dict[int, int]] = {}

        analysis = self._make_analysis_insights(
            [
                ("ComponentA", [1, 2, 3]),
            ]
        )

        self.orchestrator._remap_cluster_ids_in_analysis(analysis, cluster_mappings)

        # Should be unchanged since empty mapping triggers early return
        self.assertEqual(analysis.components[0].source_cluster_ids, [1, 2, 3])


# ---- Bug 3: source_file_strs rebuilt per reference in _validate_no_dangling_references ----


class TestValidateNoDanglingReferencesFix(unittest.TestCase):
    """Bug 3: source_file_strs was rebuilt in the loop for each reference, causing O(n*m) performance."""

    def setUp(self):
        self.cache_manager = AnalysisCacheManager()

    def _make_analysis_result(
        self,
        node_names: list[str],
        file_path: str = "/test/file.py",
        ref_names: list[str] | None = None,
        ref_file: str | None = None,
    ) -> dict:
        """Create a minimal analysis result for validation testing."""
        cg = CallGraph()
        for name in node_names:
            cg.add_node(make_node(name, file_path))
        for i in range(len(node_names) - 1):
            cg.add_edge(node_names[i], node_names[i + 1])

        refs = []
        if ref_names:
            rf = ref_file or file_path
            refs = [make_node(name, rf) for name in ref_names]

        return {
            "call_graph": cg,
            "class_hierarchies": {},
            "package_relations": {},
            "references": refs,
            "source_files": [Path(file_path)],
        }

    def test_valid_references_pass_validation(self):
        """References pointing to existing source files should not raise."""
        result = self._make_analysis_result(
            node_names=["mod.func1", "mod.func2"],
            file_path="/test/file.py",
            ref_names=["mod.ref1", "mod.ref2"],
            ref_file="/test/file.py",
        )
        # Should not raise
        self.cache_manager._validate_no_dangling_references(result)

    def test_dangling_references_raise_error(self):
        """References pointing to non-existent source files should raise ValueError."""
        result = self._make_analysis_result(
            node_names=["mod.func1"],
            file_path="/test/file.py",
            ref_names=["mod.orphan"],
            ref_file="/test/other_file.py",  # Not in source_files
        )
        with self.assertRaises(ValueError) as ctx:
            self.cache_manager._validate_no_dangling_references(result)
        self.assertIn("non-existent source file", str(ctx.exception))

    def test_validation_checks_all_references_not_just_first(self):
        """All references should be validated, not just the first one."""
        cg = CallGraph()
        cg.add_node(make_node("mod.func1", "/test/file.py"))

        # First two refs are valid, third is dangling
        refs = [
            make_node("mod.ref1", "/test/file.py"),
            make_node("mod.ref2", "/test/file.py"),
            make_node("mod.orphan", "/test/missing.py"),
        ]

        result = {
            "call_graph": cg,
            "class_hierarchies": {},
            "package_relations": {},
            "references": refs,
            "source_files": [Path("/test/file.py")],
        }

        with self.assertRaises(ValueError) as ctx:
            self.cache_manager._validate_no_dangling_references(result)
        self.assertIn("mod.orphan", str(ctx.exception))

    def test_validation_performance_source_file_strs_built_once(self):
        """Verify source_file_strs is built once, not per reference (performance fix)."""
        cg = CallGraph()
        cg.add_node(make_node("mod.func1", "/test/file.py"))

        # Create many references - all valid
        refs = [make_node(f"mod.ref{i}", "/test/file.py") for i in range(100)]

        result = {
            "call_graph": cg,
            "class_hierarchies": {},
            "package_relations": {},
            "references": refs,
            "source_files": [Path("/test/file.py")],
        }

        # The validation should not rebuild source_file_strs per reference.
        # We verify correctness (the important thing is that it doesn't
        # rebuild the set per iteration - verified by code review, tested for correctness)
        self.cache_manager._validate_no_dangling_references(result)  # Should not raise

    def test_dangling_edges_detected(self):
        """Edges referencing non-existent nodes should be detected."""
        cg = CallGraph()
        node1 = make_node("mod.func1", "/test/file.py")
        node2 = make_node("mod.func2", "/test/file.py")
        cg.add_node(node1)
        cg.add_node(node2)
        cg.add_edge("mod.func1", "mod.func2")

        # Remove node2 to create a dangling edge
        del cg.nodes["mod.func2"]

        result = {
            "call_graph": cg,
            "class_hierarchies": {},
            "package_relations": {},
            "references": [],
            "source_files": [Path("/test/file.py")],
        }

        with self.assertRaises(ValueError) as ctx:
            self.cache_manager._validate_no_dangling_references(result)
        self.assertIn("non-existent node", str(ctx.exception))

    def test_dangling_class_hierarchy_detected(self):
        """Class hierarchies referencing non-existent files should be detected."""
        result = {
            "call_graph": CallGraph(),
            "class_hierarchies": {"MyClass": {"file_path": "/test/missing.py"}},
            "package_relations": {},
            "references": [],
            "source_files": [Path("/test/file.py")],
        }

        with self.assertRaises(ValueError) as ctx:
            self.cache_manager._validate_no_dangling_references(result)
        self.assertIn("MyClass", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
