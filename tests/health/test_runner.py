import unittest

from health.models import HealthCheckConfig, StandardCheckSummary
from health.runner import run_health_checks
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import CallGraph, Node


def _make_node(fqn: str, file_path: str, line_start: int, line_end: int) -> Node:
    return Node(
        fully_qualified_name=fqn,
        node_type=12,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
    )


class TestHealthRunner(unittest.TestCase):
    def test_full_report_generation(self):
        """Test that the runner produces a valid HealthReport from StaticAnalysisResults."""
        # Build a simple call graph
        graph = CallGraph(language="python")
        graph.add_node(_make_node("mod.small_func", "/src/mod.py", 0, 10))
        graph.add_node(_make_node("mod.large_func", "/src/mod.py", 10, 120))
        graph.add_node(_make_node("mod.caller", "/src/mod.py", 120, 140))
        graph.add_node(_make_node("mod.orphan", "/src/orphan.py", 0, 5))
        graph.add_edge("mod.caller", "mod.small_func")
        graph.add_edge("mod.caller", "mod.large_func")

        # Build StaticAnalysisResults
        results = StaticAnalysisResults()
        results.add_cfg("python", graph)
        results.add_references("python", list(graph.nodes.values()))
        results.add_source_files("python", ["/src/mod.py", "/src/orphan.py"])

        hierarchy = {
            "mod": {
                "superclasses": [],
                "subclasses": [],
                "file_path": "/src/mod.py",
                "line_start": 0,
                "line_end": 140,
            }
        }
        results.add_class_hierarchy("python", hierarchy)

        pkg_deps = {
            "mod": {"imports": ["os", "sys"], "imported_by": []},
        }
        results.add_package_dependencies("python", pkg_deps)

        # Use fixed thresholds for predictable test results
        config = HealthCheckConfig(
            function_size_max=100,
        )

        report = run_health_checks(results, "test-repo", config=config)

        # Check overall structure
        self.assertEqual(report.repository_name, "test-repo")
        self.assertGreaterEqual(report.overall_score, 0.0)
        self.assertLessEqual(report.overall_score, 1.0)

        # Should have check summaries
        self.assertGreater(len(report.check_summaries), 0)

        # Find function_size check
        size_summary = next(s for s in report.check_summaries if s.check_name == "function_size")
        self.assertIsNotNone(size_summary)
        assert isinstance(size_summary, StandardCheckSummary)
        # large_func is 110 lines (>100 threshold)
        self.assertEqual(size_summary.findings_count, 1)

        # Find fan_out check
        fan_out_summary = next(s for s in report.check_summaries if s.check_name == "fan_out")
        self.assertIsNotNone(fan_out_summary)
        assert isinstance(fan_out_summary, StandardCheckSummary)
        # caller calls 2 functions (threshold is high by default)
        self.assertEqual(fan_out_summary.findings_count, 0)

        # Find orphan_code check
        orphan_summary = next(s for s in report.check_summaries if s.check_name == "orphan_code")
        self.assertIsNotNone(orphan_summary)
        assert isinstance(orphan_summary, StandardCheckSummary)
        # orphan has no incoming or outgoing calls
        self.assertEqual(orphan_summary.findings_count, 1)

        # Check that report can be serialized to JSON
        import json

        json_str = report.model_dump_json()
        self.assertIsInstance(json_str, str)
        data = json.loads(json_str)
        self.assertEqual(data["repository_name"], "test-repo")

    def test_json_serialization(self):
        """Test that the HealthReport can be serialized to JSON."""
        graph = CallGraph(language="python")
        graph.add_node(_make_node("mod.func", "/src/mod.py", 0, 50))

        results = StaticAnalysisResults()
        results.add_cfg("python", graph)
        results.add_references("python", list(graph.nodes.values()))
        results.add_source_files("python", ["/src/mod.py"])

        report = run_health_checks(results, "test")

        # Serialize to JSON
        import json

        json_str = report.model_dump_json()
        data = json.loads(json_str)

        # Verify structure
        self.assertEqual(data["repository_name"], "test")
        self.assertIn("overall_score", data)
        self.assertIn("timestamp", data)
        self.assertIn("check_summaries", data)
        self.assertIn("file_summaries", data)

    def test_empty_results(self):
        """Test that empty StaticAnalysisResults produces a valid report."""
        results = StaticAnalysisResults()
        report = run_health_checks(results, "empty-repo")
        self.assertEqual(report.overall_score, 1.0)
        self.assertEqual(len(report.check_summaries), 0)

    def test_custom_config(self):
        """Test that custom config thresholds are respected."""
        graph = CallGraph(language="python")
        graph.add_node(_make_node("mod.func", "/f.py", 0, 40))

        results = StaticAnalysisResults()
        results.add_cfg("python", graph)
        results.add_references("python", list(graph.nodes.values()))
        results.add_source_files("python", ["/f.py"])

        # With default fixed threshold (max=500), no finding
        config_default = HealthCheckConfig(
            function_size_max=500,
        )
        report_default = run_health_checks(results, "test", config=config_default)
        size_default = next(s for s in report_default.check_summaries if s.check_name == "function_size")
        assert isinstance(size_default, StandardCheckSummary)
        self.assertEqual(size_default.findings_count, 0)

        # With lower threshold, should find it
        config = HealthCheckConfig(
            function_size_max=30,
        )
        report_custom = run_health_checks(results, "test", config=config)
        size_custom = next(s for s in report_custom.check_summaries if s.check_name == "function_size")
        assert isinstance(size_custom, StandardCheckSummary)
        self.assertEqual(size_custom.findings_count, 1)

    def test_file_summaries_aggregation(self):
        """Test that file-level summaries aggregate correctly."""
        graph = CallGraph(language="python")
        graph.add_node(_make_node("mod.func1", "/src/mod.py", 0, 120))
        graph.add_node(_make_node("mod.func2", "/src/mod.py", 120, 250))

        results = StaticAnalysisResults()
        results.add_cfg("python", graph)
        results.add_references("python", list(graph.nodes.values()))
        results.add_source_files("python", ["/src/mod.py"])

        config = HealthCheckConfig(
            function_size_max=100,
        )
        report = run_health_checks(results, "test", config=config)

        # Should have file summaries
        file_sums = report.file_summaries
        self.assertGreater(len(file_sums), 0)

        # The file with violations should have findings
        mod_file = next((f for f in file_sums if "mod.py" in f.file_path), None)
        self.assertIsNotNone(mod_file)
        assert mod_file is not None
        self.assertGreater(mod_file.total_findings, 0)

    def test_relative_paths_when_repo_path_provided(self):
        """Test that file paths are relative to repo_path when provided."""
        graph = CallGraph(language="python")
        graph.add_node(_make_node("mod.func1", "/home/user/project/src/mod.py", 0, 120))
        graph.add_node(_make_node("mod.func2", "/home/user/project/src/mod.py", 120, 250))
        graph.add_node(_make_node("utils.helper", "/home/user/project/lib/utils.py", 0, 10))

        results = StaticAnalysisResults()
        results.add_cfg("python", graph)
        results.add_references("python", list(graph.nodes.values()))
        results.add_source_files(
            "python",
            ["/home/user/project/src/mod.py", "/home/user/project/lib/utils.py"],
        )

        config = HealthCheckConfig(
            function_size_max=100,
        )
        report = run_health_checks(results, "test", config=config, repo_path="/home/user/project")

        # All file paths in findings should be relative
        for summary in report.check_summaries:
            if hasattr(summary, "finding_groups"):
                for group in summary.finding_groups:  # type: ignore[attr-defined]
                    for entity in group.entities:
                        if entity.file_path is not None:
                            self.assertFalse(
                                entity.file_path.startswith("/home/user/project"),
                                f"Expected relative path, got: {entity.file_path}",
                            )

    def test_absolute_paths_when_no_repo_path(self):
        """Test that file paths remain absolute when repo_path is not provided."""
        graph = CallGraph(language="python")
        graph.add_node(_make_node("mod.func", "/home/user/project/src/mod.py", 0, 120))

        results = StaticAnalysisResults()
        results.add_cfg("python", graph)
        results.add_references("python", list(graph.nodes.values()))
        results.add_source_files("python", ["/home/user/project/src/mod.py"])

        config = HealthCheckConfig(
            function_size_max=100,
        )
        report = run_health_checks(results, "test", config=config)

        # File paths should remain absolute
        for summary in report.check_summaries:
            if hasattr(summary, "finding_groups"):
                for group in summary.finding_groups:  # type: ignore[attr-defined]
                    for entity in group.entities:
                        if entity.file_path is not None:
                            self.assertTrue(
                                entity.file_path.startswith("/"),
                                f"Expected absolute path, got: {entity.file_path}",
                            )


if __name__ == "__main__":
    unittest.main()
