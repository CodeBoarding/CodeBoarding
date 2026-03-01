import unittest

from agents.validation import (
    ValidationContext,
    ValidationResult,
    validate_cluster_coverage,
    validate_file_classifications,
    validate_relation_component_names,
    _check_edge_between_cluster_sets,
)
from agents.agent_responses import (
    ClusterAnalysis,
    ClustersComponent,
    AnalysisInsights,
    Component,
    Relation,
    ComponentFiles,
    FileClassification,
)
from static_analyzer.graph import ClusterResult, CallGraph
from static_analyzer.node import Node


class TestValidationContext(unittest.TestCase):
    """Test ValidationContext dataclass initialization."""

    def test_default_initialization(self):
        context = ValidationContext()
        self.assertEqual(context.cluster_results, {})
        self.assertEqual(context.cfg_graphs, {})
        self.assertEqual(context.expected_cluster_ids, set())
        self.assertEqual(context.expected_files, set())
        self.assertEqual(context.valid_component_names, set())
        self.assertIsNone(context.repo_dir)

    def test_initialization_with_values(self):
        cluster_results = {"python": ClusterResult()}
        cfg_graphs = {"python": CallGraph()}
        expected_ids = {1, 2, 3}
        expected_files = {"file1.py", "file2.py"}
        valid_names = {"ComponentA", "ComponentB"}
        repo_dir = "/path/to/repo"

        context = ValidationContext(
            cluster_results=cluster_results,
            cfg_graphs=cfg_graphs,
            expected_cluster_ids=expected_ids,
            expected_files=expected_files,
            valid_component_names=valid_names,
            repo_dir=repo_dir,
        )

        self.assertEqual(context.cluster_results, cluster_results)
        self.assertEqual(context.cfg_graphs, cfg_graphs)
        self.assertEqual(context.expected_cluster_ids, expected_ids)
        self.assertEqual(context.expected_files, expected_files)
        self.assertEqual(context.valid_component_names, valid_names)
        self.assertEqual(context.repo_dir, repo_dir)


class TestValidationResult(unittest.TestCase):
    """Test ValidationResult dataclass."""

    def test_valid_result(self):
        result = ValidationResult(is_valid=True)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.feedback_messages, [])

    def test_invalid_result_with_feedback(self):
        feedback = ["Error 1", "Error 2"]
        result = ValidationResult(is_valid=False, feedback_messages=feedback)
        self.assertFalse(result.is_valid)
        self.assertEqual(result.feedback_messages, feedback)


class TestValidateClusterCoverage(unittest.TestCase):
    """Test validate_cluster_coverage function."""

    def test_all_clusters_covered(self):
        cluster_analysis = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(name="Component A", description="desc", cluster_ids=[1, 2]),
                ClustersComponent(name="Component B", description="desc", cluster_ids=[3, 4]),
            ]
        )
        context = ValidationContext(expected_cluster_ids={1, 2, 3, 4})
        result = validate_cluster_coverage(cluster_analysis, context)
        self.assertTrue(result.is_valid)

    def test_missing_clusters(self):
        cluster_analysis = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(name="Component A", description="desc", cluster_ids=[1, 2]),
            ]
        )
        context = ValidationContext(expected_cluster_ids={1, 2, 3, 4, 5})
        result = validate_cluster_coverage(cluster_analysis, context)
        self.assertFalse(result.is_valid)
        self.assertIn("3, 4, 5", result.feedback_messages[0])

    def test_no_expected_clusters(self):
        cluster_analysis = ClusterAnalysis(cluster_components=[])
        context = ValidationContext(expected_cluster_ids=set())
        result = validate_cluster_coverage(cluster_analysis, context)
        self.assertTrue(result.is_valid)

    def test_empty_cluster_analysis(self):
        cluster_analysis = ClusterAnalysis(cluster_components=[])
        context = ValidationContext(expected_cluster_ids={1, 2, 3})
        result = validate_cluster_coverage(cluster_analysis, context)
        self.assertFalse(result.is_valid)
        self.assertIn("1, 2, 3", result.feedback_messages[0])

    def test_overlapping_cluster_ids(self):
        cluster_analysis = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(name="Component A", description="desc", cluster_ids=[1, 2, 3]),
                ClustersComponent(name="Component B", description="desc", cluster_ids=[2, 3, 4]),
            ]
        )
        context = ValidationContext(expected_cluster_ids={1, 2, 3, 4})
        result = validate_cluster_coverage(cluster_analysis, context)
        self.assertTrue(result.is_valid)


class TestValidateFileClassifications(unittest.TestCase):
    """Test validate_file_classifications function."""

    def test_all_files_classified_with_valid_names(self):
        """Test when all files are classified with valid component names."""
        component_files = ComponentFiles(
            file_paths=[
                FileClassification(file_path="src/file1.py", component_name="ComponentA"),
                FileClassification(file_path="src/file2.py", component_name="ComponentB"),
            ]
        )

        context = ValidationContext(
            expected_files={"src/file1.py", "src/file2.py"},
            valid_component_names={"ComponentA", "ComponentB", "ComponentC"},
        )

        result = validate_file_classifications(component_files, context)

        self.assertTrue(result.is_valid)
        self.assertEqual(result.feedback_messages, [])

    def test_missing_files(self):
        """Test when some files are not classified."""
        component_files = ComponentFiles(
            file_paths=[
                FileClassification(file_path="src/file1.py", component_name="ComponentA"),
            ]
        )

        context = ValidationContext(
            expected_files={"src/file1.py", "src/file2.py", "src/file3.py"},
            valid_component_names={"ComponentA"},
        )

        result = validate_file_classifications(component_files, context)

        self.assertFalse(result.is_valid)
        self.assertEqual(len(result.feedback_messages), 1)
        self.assertIn("file2.py", result.feedback_messages[0])
        self.assertIn("file3.py", result.feedback_messages[0])
        self.assertIn("not classified", result.feedback_messages[0])

    def test_invalid_component_names(self):
        """Test when files are classified with invalid component names."""
        component_files = ComponentFiles(
            file_paths=[
                FileClassification(file_path="src/file1.py", component_name="InvalidComponent"),
                FileClassification(file_path="src/file2.py", component_name="ComponentA"),
            ]
        )

        context = ValidationContext(
            expected_files={"src/file1.py", "src/file2.py"},
            valid_component_names={"ComponentA", "ComponentB"},
        )

        result = validate_file_classifications(component_files, context)

        self.assertFalse(result.is_valid)
        self.assertEqual(len(result.feedback_messages), 1)
        self.assertIn("InvalidComponent", result.feedback_messages[0])
        self.assertIn("Invalid component names", result.feedback_messages[0])
        self.assertIn("ComponentA", result.feedback_messages[0])  # Shows valid names

    def test_both_missing_files_and_invalid_names(self):
        """Test when there are both missing files and invalid component names."""
        component_files = ComponentFiles(
            file_paths=[
                FileClassification(file_path="src/file1.py", component_name="InvalidComponent"),
            ]
        )

        context = ValidationContext(
            expected_files={"src/file1.py", "src/file2.py"},
            valid_component_names={"ComponentA"},
        )

        result = validate_file_classifications(component_files, context)

        self.assertFalse(result.is_valid)
        self.assertEqual(len(result.feedback_messages), 2)  # Both types of errors

    def test_no_expected_files(self):
        """Test when no expected files are provided."""
        component_files = ComponentFiles(file_paths=[])
        context = ValidationContext(expected_files=set())

        result = validate_file_classifications(component_files, context)

        self.assertTrue(result.is_valid)

    def test_path_normalization_with_repo_dir(self):
        """Test path normalization when repo_dir is provided."""
        component_files = ComponentFiles(
            file_paths=[
                FileClassification(file_path="src/file1.py", component_name="ComponentA"),
            ]
        )

        context = ValidationContext(
            expected_files={"/repo/src/file1.py"},  # Absolute path
            valid_component_names={"ComponentA"},
            repo_dir="/repo",
        )

        result = validate_file_classifications(component_files, context)

        self.assertTrue(result.is_valid)  # Should normalize /repo/src/file1.py to src/file1.py

    def test_truncate_long_error_lists(self):
        """Test that long error lists are truncated."""
        # Create 15 missing files
        expected_files = {f"file{i}.py" for i in range(15)}

        component_files = ComponentFiles(file_paths=[])

        context = ValidationContext(
            expected_files=expected_files,
            valid_component_names={"ComponentA"},
        )

        result = validate_file_classifications(component_files, context)

        self.assertFalse(result.is_valid)
        # Should mention truncation
        self.assertIn("and 5 more", result.feedback_messages[0])


class TestCheckEdgeBetweenClusterSets(unittest.TestCase):
    """Test _check_edge_between_cluster_sets helper function."""

    def test_edge_exists_between_clusters(self):
        """Test when an edge exists between cluster sets."""
        cfg = CallGraph(language="python")
        node1 = Node("module.Class1.method1", 6, "file1.py", 1, 10)
        node2 = Node("module.Class2.method2", 6, "file2.py", 1, 10)

        cfg.add_node(node1)
        cfg.add_node(node2)
        cfg.add_edge("module.Class1.method1", "module.Class2.method2")

        cluster_result = ClusterResult(
            clusters={
                1: {"module.Class1.method1"},
                2: {"module.Class2.method2"},
            }
        )

        has_edge = _check_edge_between_cluster_sets(
            src_cluster_ids=[1],
            dst_cluster_ids=[2],
            cluster_results={"python": cluster_result},
            cfg_graphs={"python": cfg},
        )

        self.assertTrue(has_edge)

    def test_no_edge_between_clusters(self):
        """Test when no edge exists between cluster sets."""
        cfg = CallGraph(language="python")
        node1 = Node("module.Class1.method1", 6, "file1.py", 1, 10)
        node2 = Node("module.Class2.method2", 6, "file2.py", 1, 10)
        node3 = Node("module.Class3.method3", 6, "file3.py", 1, 10)

        cfg.add_node(node1)
        cfg.add_node(node2)
        cfg.add_node(node3)
        cfg.add_edge("module.Class1.method1", "module.Class2.method2")  # 1->2, but we check 1->3

        cluster_result = ClusterResult(
            clusters={
                1: {"module.Class1.method1"},
                2: {"module.Class2.method2"},
                3: {"module.Class3.method3"},
            }
        )

        has_edge = _check_edge_between_cluster_sets(
            src_cluster_ids=[1],
            dst_cluster_ids=[3],  # No edge from 1 to 3
            cluster_results={"python": cluster_result},
            cfg_graphs={"python": cfg},
        )

        self.assertFalse(has_edge)

    def test_multiple_clusters_with_one_edge(self):
        """Test when multiple clusters are provided and at least one pair has an edge."""
        cfg = CallGraph(language="python")
        node1 = Node("module.Class1.method1", 6, "file1.py", 1, 10)
        node2 = Node("module.Class2.method2", 6, "file2.py", 1, 10)
        node3 = Node("module.Class3.method3", 6, "file3.py", 1, 10)

        cfg.add_node(node1)
        cfg.add_node(node2)
        cfg.add_node(node3)
        cfg.add_edge("module.Class1.method1", "module.Class2.method2")

        cluster_result = ClusterResult(
            clusters={
                1: {"module.Class1.method1"},
                2: {"module.Class2.method2"},
                3: {"module.Class3.method3"},
            }
        )

        has_edge = _check_edge_between_cluster_sets(
            src_cluster_ids=[1, 3],  # Multiple source clusters
            dst_cluster_ids=[2],
            cluster_results={"python": cluster_result},
            cfg_graphs={"python": cfg},
        )

        self.assertTrue(has_edge)  # Because 1->2 exists

    def test_empty_cluster_sets(self):
        """Test when cluster sets are empty."""
        cfg = CallGraph(language="python")
        cluster_result = ClusterResult(clusters={})

        has_edge = _check_edge_between_cluster_sets(
            src_cluster_ids=[],
            dst_cluster_ids=[],
            cluster_results={"python": cluster_result},
            cfg_graphs={"python": cfg},
        )

        self.assertFalse(has_edge)

    def test_node_not_in_cluster(self):
        """Test when graph has nodes not assigned to any cluster."""
        cfg = CallGraph(language="python")
        node1 = Node("module.Class1.method1", 6, "file1.py", 1, 10)
        node2 = Node("module.Class2.method2", 6, "file2.py", 1, 10)

        cfg.add_node(node1)
        cfg.add_node(node2)
        cfg.add_edge("module.Class1.method1", "module.Class2.method2")

        # Only node1 is in a cluster, node2 is not
        cluster_result = ClusterResult(
            clusters={
                1: {"module.Class1.method1"},
            }
        )

        has_edge = _check_edge_between_cluster_sets(
            src_cluster_ids=[1],
            dst_cluster_ids=[2],  # Cluster 2 doesn't exist
            cluster_results={"python": cluster_result},
            cfg_graphs={"python": cfg},
        )

        self.assertFalse(has_edge)

    def test_multiple_languages(self):
        """Test when checking across multiple languages."""
        # Python graph with edge 1->2
        cfg_python = CallGraph(language="python")
        node1 = Node("module.Class1.method1", 6, "file1.py", 1, 10)
        node2 = Node("module.Class2.method2", 6, "file2.py", 1, 10)
        cfg_python.add_node(node1)
        cfg_python.add_node(node2)
        cfg_python.add_edge("module.Class1.method1", "module.Class2.method2")

        cluster_result_python = ClusterResult(
            clusters={
                1: {"module.Class1.method1"},
                2: {"module.Class2.method2"},
            }
        )

        # JavaScript graph with no edges
        cfg_js = CallGraph(language="javascript")
        node3 = Node("module.Class3.method3", 6, "file3.js", 1, 10)
        cfg_js.add_node(node3)

        cluster_result_js = ClusterResult(
            clusters={
                3: {"module.Class3.method3"},
            }
        )

        has_edge = _check_edge_between_cluster_sets(
            src_cluster_ids=[1],
            dst_cluster_ids=[2],
            cluster_results={"python": cluster_result_python, "javascript": cluster_result_js},
            cfg_graphs={"python": cfg_python, "javascript": cfg_js},
        )

        self.assertTrue(has_edge)  # Found in python

    def test_reverse_direction_edge(self):
        """Test that edge is found when checking in reverse direction (dst->src exists but we query src->dst)."""
        cfg = CallGraph(language="python")
        node1 = Node("module.Class1.method1", 6, "file1.py", 1, 10)
        node2 = Node("module.Class2.method2", 6, "file2.py", 1, 10)

        cfg.add_node(node1)
        cfg.add_node(node2)
        cfg.add_edge("module.Class1.method1", "module.Class2.method2")  # Edge: 1->2

        cluster_result = ClusterResult(
            clusters={
                1: {"module.Class1.method1"},
                2: {"module.Class2.method2"},
            }
        )

        # Query reversed: src=2, dst=1 — but edge is 1->2
        has_edge = _check_edge_between_cluster_sets(
            src_cluster_ids=[2],
            dst_cluster_ids=[1],
            cluster_results={"python": cluster_result},
            cfg_graphs={"python": cfg},
        )

        self.assertTrue(has_edge)


class TestValidateRelationComponentNames(unittest.TestCase):
    """Test validate_relation_component_names function."""

    def _make_analysis(self, component_names: list[str], relations: list[tuple[str, str, str]]) -> AnalysisInsights:
        components = [Component(name=n, description="desc", key_entities=[]) for n in component_names]
        rel_objs = [Relation(relation=rel, src_name=src, dst_name=dst) for rel, src, dst in relations]
        return AnalysisInsights(description="Test", components=components, components_relations=rel_objs)

    def test_all_relation_names_valid(self):
        """Relations whose src_name and dst_name match existing components should pass."""
        analysis = self._make_analysis(
            ["CompA", "CompB"],
            [("calls", "CompA", "CompB")],
        )
        result = validate_relation_component_names(analysis, ValidationContext())
        self.assertTrue(result.is_valid)
        self.assertEqual(result.feedback_messages, [])

    def test_invalid_src_name(self):
        """A relation with a src_name that does not match any component should fail."""
        analysis = self._make_analysis(
            ["CompA", "CompB"],
            [("calls", "NonExistent", "CompB")],
        )
        result = validate_relation_component_names(analysis, ValidationContext())
        self.assertFalse(result.is_valid)
        self.assertEqual(len(result.feedback_messages), 1)
        self.assertIn("NonExistent", result.feedback_messages[0])

    def test_invalid_dst_name(self):
        """A relation with a dst_name that does not match any component should fail."""
        analysis = self._make_analysis(
            ["CompA", "CompB"],
            [("calls", "CompA", "Ghost")],
        )
        result = validate_relation_component_names(analysis, ValidationContext())
        self.assertFalse(result.is_valid)
        self.assertEqual(len(result.feedback_messages), 1)
        self.assertIn("Ghost", result.feedback_messages[0])

    def test_both_names_invalid(self):
        """A relation where both src_name and dst_name are unknown should flag both."""
        analysis = self._make_analysis(
            ["CompA"],
            [("calls", "GhostSrc", "GhostDst")],
        )
        result = validate_relation_component_names(analysis, ValidationContext())
        self.assertFalse(result.is_valid)
        self.assertIn("GhostSrc", result.feedback_messages[0])
        self.assertIn("GhostDst", result.feedback_messages[0])

    def test_multiple_invalid_relations(self):
        """Multiple relations with unknown names should all be reported."""
        analysis = self._make_analysis(
            ["CompA", "CompB"],
            [
                ("calls", "CompA", "Missing1"),
                ("uses", "Missing2", "CompB"),
            ],
        )
        result = validate_relation_component_names(analysis, ValidationContext())
        self.assertFalse(result.is_valid)
        self.assertIn("Missing1", result.feedback_messages[0])
        self.assertIn("Missing2", result.feedback_messages[0])

    def test_no_relations(self):
        """No relations should always pass."""
        analysis = self._make_analysis(["CompA"], [])
        result = validate_relation_component_names(analysis, ValidationContext())
        self.assertTrue(result.is_valid)

    def test_empty_components_with_relations(self):
        """Relations that reference components when no components exist should fail."""
        analysis = self._make_analysis([], [("calls", "LLM Agent Core", "Agent Tooling Interface")])
        result = validate_relation_component_names(analysis, ValidationContext())
        self.assertFalse(result.is_valid)
        self.assertIn("LLM Agent Core", result.feedback_messages[0])
        self.assertIn("Agent Tooling Interface", result.feedback_messages[0])


if __name__ == "__main__":
    unittest.main()
