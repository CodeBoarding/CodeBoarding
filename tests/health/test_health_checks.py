import tempfile
import unittest
from pathlib import Path

from health.checks.circular_deps import check_circular_dependencies
from health.checks.function_size import check_function_size
from health.models import HealthCheckConfig, Severity
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.cfg import CallGraph
from static_analyzer.config import NodeType
from static_analyzer.node import Node


_REPO_ROOT = Path("/project")
# No .codeboardingignore under this root, so the default template patterns apply.
_IGNORE_MANAGER = RepoIgnoreManager(_REPO_ROOT)


def _make_node(fqn: str, file_path: str, line_start: int, line_end: int, node_type: int = 12) -> Node:
    return Node(
        fully_qualified_name=fqn,
        node_type=node_type,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
    )


def _build_simple_graph() -> CallGraph:
    """Build a small call graph for testing:
    A -> B -> C
    A -> D
    E (orphan)
    """
    graph = CallGraph()
    graph.add_node(_make_node("mod.A", "/src/a.py", 0, 30))
    graph.add_node(_make_node("mod.B", "/src/b.py", 0, 10))
    graph.add_node(_make_node("mod.C", "/src/c.py", 0, 5))
    graph.add_node(_make_node("mod.D", "/src/d.py", 0, 8))
    graph.add_node(_make_node("mod.E", "/src/e.py", 0, 3))

    graph.add_edge("mod.A", "mod.B")
    graph.add_edge("mod.A", "mod.D")
    graph.add_edge("mod.B", "mod.C")
    return graph


class TestFunctionSize(unittest.TestCase):
    def test_no_findings_below_threshold(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.small", "/project/f.py", 0, 10))
        config = HealthCheckConfig(function_size_max=100)
        result = check_function_size(graph, config, _IGNORE_MANAGER)
        self.assertEqual(result.findings_count, 0)
        self.assertEqual(result.score, 1.0)

    def test_warning_finding(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.medium", "/project/f.py", 0, 60))
        config = HealthCheckConfig(
            function_size_max=50,
        )
        result = check_function_size(graph, config, _IGNORE_MANAGER)
        self.assertEqual(result.findings_count, 1)
        self.assertEqual(result.finding_groups[0].severity, Severity.WARNING)
        self.assertEqual(result.finding_groups[0].entities[0].metric_value, 60.0)

    def test_above_threshold_is_warning(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.large", "/project/f.py", 0, 150))
        config = HealthCheckConfig(
            function_size_max=100,
        )
        result = check_function_size(graph, config, _IGNORE_MANAGER)
        entity_names = {f.entity_name for f in result.findings}
        self.assertIn("mod.large", entity_names)
        self.assertEqual(result.total_entities_checked, 1)

    def test_function_size_skips_data_entities(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.MY_CONSTANT", "/project/f.py", 0, 100, node_type=NodeType.CONSTANT))
        graph.add_node(_make_node("mod.my_var", "/project/f.py", 0, 100, node_type=NodeType.VARIABLE))
        graph.add_node(_make_node("mod.Class.prop", "/project/f.py", 0, 100, node_type=NodeType.PROPERTY))
        config = HealthCheckConfig(
            function_size_max=100,
        )
        result = check_function_size(graph, config, _IGNORE_MANAGER)
        self.assertEqual(result.findings_count, 0)
        self.assertEqual(result.total_entities_checked, 0)

    def test_empty_graph(self):
        graph = CallGraph()
        result = check_function_size(graph, HealthCheckConfig(), _IGNORE_MANAGER)
        self.assertEqual(result.total_entities_checked, 0)
        self.assertEqual(result.score, 1.0)

    def test_zero_size_skipped(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.zero", "/project/f.py", 10, 10))
        result = check_function_size(graph, HealthCheckConfig(), _IGNORE_MANAGER)
        self.assertEqual(result.total_entities_checked, 0)


class TestCircularDependencies(unittest.TestCase):
    def test_cycle_detected(self):
        pkg_deps = {
            "pkg_a": {"imports": ["pkg_b"], "imported_by": ["pkg_b"]},
            "pkg_b": {"imports": ["pkg_a"], "imported_by": ["pkg_a"]},
        }
        config = HealthCheckConfig()
        summary = check_circular_dependencies(pkg_deps, config)
        self.assertGreater(len(summary.cycles), 0)
        self.assertEqual(summary.packages_checked, 2)
        self.assertEqual(summary.packages_in_cycles, 2)

    def test_no_cycle(self):
        pkg_deps = {
            "pkg_a": {"imports": ["pkg_b"], "imported_by": []},
            "pkg_b": {"imports": [], "imported_by": ["pkg_a"]},
        }
        config = HealthCheckConfig()
        summary = check_circular_dependencies(pkg_deps, config)
        self.assertEqual(len(summary.cycles), 0)
        self.assertEqual(summary.packages_in_cycles, 0)

    def test_prefers_import_deps_over_imports(self):
        """When import_deps is present, cycle detection should use it instead of imports."""
        pkg_deps = {
            "pkg_a": {
                "imports": ["pkg_b"],
                "import_deps": [],  # No import-based dep on pkg_b
                "imported_by": [],
            },
            "pkg_b": {
                "imports": ["pkg_a"],
                "import_deps": ["pkg_a"],  # Only pkg_b imports pkg_a
                "imported_by": [],
            },
        }
        config = HealthCheckConfig()
        summary = check_circular_dependencies(pkg_deps, config)
        # No cycle because import_deps is unidirectional (only pkg_b -> pkg_a)
        self.assertEqual(len(summary.cycles), 0)

    def test_per_file_root_packages_no_false_cycle(self):
        """Per-file root packages should not create false cycles via a shared 'root' bucket."""
        # Simulates: main.py imports output_generators, output_generators imports utils.py
        # With the old 'root' bucket, both main and utils would be 'root' -> false cycle.
        pkg_deps = {
            "main": {"import_deps": ["output_generators"], "imported_by": []},
            "utils": {"import_deps": [], "imported_by": ["output_generators"]},
            "output_generators": {"import_deps": ["utils"], "imported_by": ["main"]},
        }
        config = HealthCheckConfig()
        summary = check_circular_dependencies(pkg_deps, config)
        self.assertEqual(len(summary.cycles), 0)


class TestEntityTypeFiltering(unittest.TestCase):
    """Tests that health checks correctly filter out classes and data entities."""

    def test_function_size_skips_classes(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.BigClass", "/project/f.py", 0, 500, node_type=NodeType.CLASS))
        graph.add_node(_make_node("mod.BigClass.big_method", "/project/f.py", 0, 200, node_type=NodeType.METHOD))
        config = HealthCheckConfig(
            function_size_max=100,
        )
        result = check_function_size(graph, config, _IGNORE_MANAGER)
        entity_names = {f.entity_name for f in result.findings}
        self.assertNotIn("mod.BigClass", entity_names)
        self.assertIn("mod.BigClass.big_method", entity_names)
        self.assertEqual(result.total_entities_checked, 1)

    def test_function_size_skips_data_entities(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.MY_CONSTANT", "/project/f.py", 0, 100, node_type=NodeType.CONSTANT))
        graph.add_node(_make_node("mod.my_var", "/project/f.py", 0, 100, node_type=NodeType.VARIABLE))
        graph.add_node(_make_node("mod.Class.prop", "/project/f.py", 0, 100, node_type=NodeType.PROPERTY))
        config = HealthCheckConfig(
            function_size_max=100,
        )
        result = check_function_size(graph, config, _IGNORE_MANAGER)
        self.assertEqual(result.total_entities_checked, 0)
        self.assertEqual(result.findings_count, 0)

    def test_entity_label_on_node(self):
        func_node = _make_node("mod.func", "/f.py", 0, 10, node_type=NodeType.FUNCTION)
        method_node = _make_node("mod.Class.method", "/f.py", 0, 10, node_type=NodeType.METHOD)
        class_node = _make_node("mod.MyClass", "/f.py", 0, 100, node_type=NodeType.CLASS)
        prop_node = _make_node("mod.Class.prop", "/f.py", 0, 5, node_type=NodeType.PROPERTY)
        const_node = _make_node("mod.CONST", "/f.py", 0, 5, node_type=NodeType.CONSTANT)

        self.assertEqual(func_node.entity_label(), "Function")
        self.assertEqual(method_node.entity_label(), "Method")
        self.assertEqual(class_node.entity_label(), "Class")
        self.assertEqual(prop_node.entity_label(), "Property")
        self.assertEqual(const_node.entity_label(), "Constant")

    def test_node_type_predicates(self):
        func_node = _make_node("mod.func", "/f.py", 0, 10, node_type=NodeType.FUNCTION)
        class_node = _make_node("mod.MyClass", "/f.py", 0, 100, node_type=NodeType.CLASS)
        prop_node = _make_node("mod.prop", "/f.py", 0, 5, node_type=NodeType.PROPERTY)

        self.assertTrue(func_node.is_callable())
        self.assertFalse(func_node.is_class())
        self.assertFalse(func_node.is_data())

        self.assertFalse(class_node.is_callable())
        self.assertTrue(class_node.is_class())
        self.assertFalse(class_node.is_data())

        self.assertFalse(prop_node.is_callable())
        self.assertFalse(prop_node.is_class())
        self.assertTrue(prop_node.is_data())


class TestHealthCheckConfig(unittest.TestCase):
    def test_default_config(self):
        config = HealthCheckConfig()
        self.assertEqual(config.function_size_max, 150)

    def test_custom_config(self):
        config = HealthCheckConfig(function_size_max=60)
        self.assertEqual(config.function_size_max, 60)


class TestFunctionSizeIgnoreFiltering(unittest.TestCase):
    def test_ignored_test_file_excluded_from_function_size(self):
        """Without a .codeboardingignore the default template applies, and it covers __tests__."""
        graph = CallGraph()
        graph.add_node(_make_node("test.big_test", "/project/__tests__/test.ts", 1, 300))
        graph.add_node(_make_node("mod.big_func", "/project/mod/utils.py", 1, 300))
        config = HealthCheckConfig(function_size_max=100)
        result = check_function_size(graph, config, _IGNORE_MANAGER)
        entity_names = {f.entity_name for f in result.findings}
        self.assertNotIn("test.big_test", entity_names)
        self.assertIn("mod.big_func", entity_names)

    def test_opted_in_test_file_is_scored(self):
        """An empty .codeboardingignore opts everything back in; only its absence falls back
        to the template."""
        with tempfile.TemporaryDirectory() as tmp:
            cb_dir = Path(tmp) / ".codeboarding"
            cb_dir.mkdir()
            (cb_dir / ".codeboardingignore").write_text("# opt everything in: no patterns\n")
            ignore_manager = RepoIgnoreManager(Path(tmp))
            graph = CallGraph()
            graph.add_node(_make_node("test.big_test", str(Path(tmp) / "__tests__" / "test.ts"), 1, 300))
            config = HealthCheckConfig(function_size_max=100)
            result = check_function_size(graph, config, ignore_manager)
        entity_names = {f.entity_name for f in result.findings}
        self.assertIn("test.big_test", entity_names)


class TestNodeCallbackDetection(unittest.TestCase):
    """Tests for Node.is_callback_or_anonymous()."""

    def test_callback_patterns(self):
        node = _make_node("mod.arr.find() callback", "/f.py", 0, 5)
        self.assertTrue(node.is_callback_or_anonymous())

    def test_anonymous_function_pattern(self):
        node = _make_node("mod.<function>", "/f.py", 0, 5)
        self.assertTrue(node.is_callback_or_anonymous())

    def test_arrow_function_pattern(self):
        node = _make_node("mod.<arrow", "/f.py", 0, 5)
        self.assertTrue(node.is_callback_or_anonymous())

    def test_normal_function(self):
        node = _make_node("mod.normal_func", "/f.py", 0, 5)
        self.assertFalse(node.is_callback_or_anonymous())


class TestLanguageFieldOnSummaries(unittest.TestCase):
    """Tests that check summaries include language when multiple languages are present."""

    def test_languages_set_on_summary(self):
        from health.models import StandardCheckSummary

        # One server serves the whole family, so a summary can cover more than one language.
        summary = StandardCheckSummary(
            check_name="test",
            description="test check",
            total_entities_checked=0,
            findings_count=0,
            score=1.0,
            languages=["javascript", "typescript"],
        )
        self.assertEqual(summary.languages, ["javascript", "typescript"])

    def test_languages_empty_by_default(self):
        from health.models import StandardCheckSummary

        summary = StandardCheckSummary(
            check_name="test",
            description="test check",
            total_entities_checked=0,
            findings_count=0,
            score=1.0,
        )
        self.assertEqual(summary.languages, [])


if __name__ == "__main__":
    unittest.main()
