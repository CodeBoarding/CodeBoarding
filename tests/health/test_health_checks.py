import unittest

from health.checks.circular_deps import check_circular_dependencies
from health.checks.cohesion import check_component_cohesion
from health.checks.coupling import check_fan_in, check_fan_out
from health.checks.function_size import check_function_size
from health.checks.god_class import check_god_classes
from health.checks.inheritance import check_inheritance_depth
from health.checks.instability import check_package_instability
from health.checks.orphan_code import check_orphan_code
from health.models import HealthCheckConfig, Severity
from static_analyzer.graph import CallGraph, Node


def _make_node(fqn: str, file_path: str, line_start: int, line_end: int, node_type: int = 12) -> Node:
    return Node(
        fully_qualified_name=fqn, node_type=node_type, file_path=file_path, line_start=line_start, line_end=line_end
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
        graph.add_node(_make_node("mod.small", "/f.py", 0, 10))
        config = HealthCheckConfig(function_size_max=100)
        result = check_function_size(graph, config)
        self.assertEqual(result.findings_count, 0)
        self.assertEqual(result.score, 1.0)

    def test_warning_finding(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.medium", "/f.py", 0, 60))
        config = HealthCheckConfig(
            function_size_max=50,
            function_size_percentile=None,
        )
        result = check_function_size(graph, config)
        self.assertEqual(result.findings_count, 1)
        self.assertEqual(result.finding_groups[0].severity, Severity.WARNING)
        self.assertEqual(result.finding_groups[0].entities[0].metric_value, 60.0)

    def test_above_threshold_is_warning(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.large", "/f.py", 0, 150))
        config = HealthCheckConfig(
            function_size_max=100,
            function_size_percentile=None,
        )
        result = check_function_size(graph, config)
        self.assertEqual(result.findings_count, 1)
        self.assertEqual(result.finding_groups[0].severity, Severity.WARNING)

    def test_empty_graph(self):
        graph = CallGraph()
        result = check_function_size(graph, HealthCheckConfig())
        self.assertEqual(result.total_entities_checked, 0)
        self.assertEqual(result.score, 1.0)

    def test_zero_size_skipped(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.zero", "/f.py", 10, 10))
        result = check_function_size(graph, HealthCheckConfig())
        self.assertEqual(result.total_entities_checked, 0)


class TestFanOut(unittest.TestCase):
    def test_high_fan_out(self):
        graph = _build_simple_graph()
        config = HealthCheckConfig(
            fan_out_max=2,
            fan_out_percentile=None,
        )
        result = check_fan_out(graph, config)
        warning_group = next((g for g in result.finding_groups if g.severity == Severity.WARNING), None)
        self.assertIsNotNone(warning_group)
        assert warning_group is not None
        fan_out_findings = [e for e in warning_group.entities if e.entity_name == "mod.A"]
        self.assertEqual(len(fan_out_findings), 1)

    def test_no_fan_out_findings(self):
        graph = _build_simple_graph()
        config = HealthCheckConfig(fan_out_max=20)
        result = check_fan_out(graph, config)
        self.assertEqual(result.findings_count, 0)


class TestFanIn(unittest.TestCase):
    def test_fan_in_detection(self):
        graph = CallGraph()
        target = _make_node("mod.target", "/f.py", 0, 10)
        graph.add_node(target)
        for i in range(5):
            caller = _make_node(f"mod.caller{i}", "/f.py", 0, 10)
            graph.add_node(caller)
            graph.add_edge(f"mod.caller{i}", "mod.target")

        config = HealthCheckConfig(
            fan_in_max=3,
            fan_in_percentile=None,
        )
        result = check_fan_in(graph, config)
        all_entities = []
        for group in result.finding_groups:
            all_entities.extend(group.entities)
        target_findings = [e for e in all_entities if e.entity_name == "mod.target"]
        self.assertEqual(len(target_findings), 1)
        self.assertEqual(target_findings[0].metric_value, 5.0)


class TestGodClass(unittest.TestCase):
    def test_god_class_by_method_count(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.BigClass", "/f.py", 0, 250, node_type=Node.CLASS_TYPE))
        for i in range(25):
            graph.add_node(
                _make_node(f"mod.BigClass.method{i}", "/f.py", i * 10, i * 10 + 5, node_type=Node.METHOD_TYPE)
            )
        config = HealthCheckConfig(
            god_class_method_count_max=20,
            god_class_loc_max=500,
            god_class_fan_out_max=30,
            god_class_method_count_percentile=None,
            god_class_loc_percentile=None,
            god_class_fan_out_percentile=None,
        )
        result = check_god_classes(graph, None, config)
        self.assertGreater(result.findings_count, 0)
        all_entities = []
        for group in result.finding_groups:
            all_entities.extend(group.entities)
        big_class_findings = [e for e in all_entities if e.entity_name == "mod.BigClass"]
        self.assertEqual(len(big_class_findings), 1)

    def test_no_god_class(self):
        graph = CallGraph()
        for i in range(3):
            graph.add_node(_make_node(f"mod.SmallClass.method{i}", "/f.py", i * 10, i * 10 + 5))
        config = HealthCheckConfig(
            god_class_method_count_max=20,
            god_class_method_count_percentile=None,
        )
        result = check_god_classes(graph, None, config)
        self.assertEqual(result.findings_count, 0)

    def test_god_class_with_hierarchy(self):
        graph = CallGraph()
        for i in range(25):
            graph.add_node(_make_node(f"mod.BigClass.method{i}", "/f.py", i * 10, i * 10 + 5))
        hierarchy = {
            "mod.BigClass": {
                "superclasses": [],
                "subclasses": [],
                "file_path": "/f.py",
                "line_start": 0,
                "line_end": 600,
            }
        }
        config = HealthCheckConfig(
            god_class_method_count_max=20,
            god_class_loc_max=500,
            god_class_method_count_percentile=None,
            god_class_loc_percentile=None,
        )
        result = check_god_classes(graph, hierarchy, config)
        self.assertGreater(result.findings_count, 0)
        warning_group = next((g for g in result.finding_groups if g.severity == Severity.WARNING), None)
        self.assertIsNotNone(warning_group)
        assert warning_group is not None
        self.assertTrue(len(warning_group.entities) > 0)


class TestInheritanceDepth(unittest.TestCase):
    def test_deep_hierarchy(self):
        hierarchy = {
            "Base": {
                "superclasses": [],
                "subclasses": ["Child1"],
                "file_path": "/f.py",
                "line_start": 0,
                "line_end": 10,
            },
            "Child1": {
                "superclasses": ["Base"],
                "subclasses": ["Child2"],
                "file_path": "/f.py",
                "line_start": 10,
                "line_end": 20,
            },
            "Child2": {
                "superclasses": ["Child1"],
                "subclasses": ["Child3"],
                "file_path": "/f.py",
                "line_start": 20,
                "line_end": 30,
            },
            "Child3": {
                "superclasses": ["Child2"],
                "subclasses": ["Child4"],
                "file_path": "/f.py",
                "line_start": 30,
                "line_end": 40,
            },
            "Child4": {
                "superclasses": ["Child3"],
                "subclasses": [],
                "file_path": "/f.py",
                "line_start": 40,
                "line_end": 50,
            },
        }
        config = HealthCheckConfig(inheritance_depth_max=3)
        result = check_inheritance_depth(hierarchy, config)
        self.assertGreater(result.findings_count, 0)
        all_entities = []
        for group in result.finding_groups:
            all_entities.extend(group.entities)
        deep_findings = [e for e in all_entities if e.entity_name == "Child4"]
        self.assertEqual(len(deep_findings), 1)

    def test_shallow_hierarchy(self):
        hierarchy = {
            "Base": {
                "superclasses": [],
                "subclasses": ["Child"],
                "file_path": "/f.py",
                "line_start": 0,
                "line_end": 10,
            },
            "Child": {
                "superclasses": ["Base"],
                "subclasses": [],
                "file_path": "/f.py",
                "line_start": 10,
                "line_end": 20,
            },
        }
        config = HealthCheckConfig(inheritance_depth_max=5)
        result = check_inheritance_depth(hierarchy, config)
        self.assertEqual(result.findings_count, 0)


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


class TestOrphanCode(unittest.TestCase):
    def test_orphan_detected(self):
        graph = _build_simple_graph()
        result = check_orphan_code(graph)
        all_entities = []
        for group in result.finding_groups:
            all_entities.extend(group.entities)
        orphan_names = {e.entity_name for e in all_entities}
        self.assertIn("mod.E", orphan_names)

    def test_no_orphans(self):
        graph = CallGraph()
        graph.add_node(_make_node("a", "/f.py", 0, 10))
        graph.add_node(_make_node("b", "/f.py", 0, 10))
        graph.add_edge("a", "b")
        result = check_orphan_code(graph)
        self.assertEqual(result.findings_count, 0)


class TestPackageInstability(unittest.TestCase):
    def test_unstable_package_with_dependents(self):
        pkg_deps = {
            "unstable": {"imports": ["dep1", "dep2", "dep3", "dep4", "dep5"], "imported_by": ["consumer"]},
            "dep1": {"imports": [], "imported_by": ["unstable"]},
            "dep2": {"imports": [], "imported_by": ["unstable"]},
            "dep3": {"imports": [], "imported_by": ["unstable"]},
            "dep4": {"imports": [], "imported_by": ["unstable"]},
            "dep5": {"imports": [], "imported_by": ["unstable"]},
            "consumer": {"imports": ["unstable"], "imported_by": []},
        }
        config = HealthCheckConfig(instability_high=0.8)
        result = check_package_instability(pkg_deps, config)
        unstable_findings = [f for f in result.findings if f.entity_name == "unstable"]
        self.assertEqual(len(unstable_findings), 1)

    def test_stable_package(self):
        pkg_deps = {
            "stable": {"imports": [], "imported_by": ["a", "b", "c"]},
            "a": {"imports": ["stable"], "imported_by": []},
            "b": {"imports": ["stable"], "imported_by": []},
            "c": {"imports": ["stable"], "imported_by": []},
        }
        config = HealthCheckConfig(instability_high=0.8)
        result = check_package_instability(pkg_deps, config)
        stable_findings = [f for f in result.findings if f.entity_name == "stable"]
        self.assertEqual(len(stable_findings), 0)


class TestComponentCohesion(unittest.TestCase):
    def test_low_cohesion(self):
        graph = CallGraph()
        graph.add_node(_make_node("a.func1", "/a.py", 0, 10))
        graph.add_node(_make_node("a.func2", "/a.py", 10, 20))
        graph.add_node(_make_node("b.func1", "/b.py", 0, 10))
        graph.add_node(_make_node("b.func2", "/b.py", 10, 20))

        graph.add_edge("a.func1", "b.func1")
        graph.add_edge("a.func2", "b.func2")
        graph.add_edge("b.func1", "a.func2")

        config = HealthCheckConfig(cohesion_low=0.1)
        result = check_component_cohesion(graph, config)
        self.assertIsNotNone(result)

    def test_empty_graph(self):
        graph = CallGraph()
        config = HealthCheckConfig()
        result = check_component_cohesion(graph, config)
        self.assertEqual(result.total_entities_checked, 0)
        self.assertEqual(result.score, 1.0)


class TestEntityTypeFiltering(unittest.TestCase):
    """Tests that health checks correctly filter out classes and data entities."""

    def test_function_size_skips_classes(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.BigClass", "/f.py", 0, 500, node_type=Node.CLASS_TYPE))
        graph.add_node(_make_node("mod.BigClass.big_method", "/f.py", 0, 200, node_type=Node.METHOD_TYPE))
        config = HealthCheckConfig(
            function_size_max=100,
            function_size_percentile=None,
        )
        result = check_function_size(graph, config)
        entity_names = {f.entity_name for f in result.findings}
        self.assertNotIn("mod.BigClass", entity_names)
        self.assertIn("mod.BigClass.big_method", entity_names)
        self.assertEqual(result.total_entities_checked, 1)

    def test_function_size_skips_data_entities(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.MY_CONSTANT", "/f.py", 0, 100, node_type=Node.CONSTANT_TYPE))
        graph.add_node(_make_node("mod.my_var", "/f.py", 0, 100, node_type=Node.VARIABLE_TYPE))
        graph.add_node(_make_node("mod.Class.prop", "/f.py", 0, 100, node_type=Node.PROPERTY_TYPE))
        config = HealthCheckConfig(
            function_size_max=100,
            function_size_percentile=None,
        )
        result = check_function_size(graph, config)
        self.assertEqual(result.total_entities_checked, 0)
        self.assertEqual(result.findings_count, 0)

    def test_fan_out_skips_classes(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.MyClass", "/f.py", 0, 100, node_type=Node.CLASS_TYPE))
        graph.add_node(_make_node("mod.func", "/f.py", 0, 10, node_type=Node.FUNCTION_TYPE))
        graph.add_node(_make_node("mod.other", "/f.py", 0, 10, node_type=Node.FUNCTION_TYPE))
        graph.add_edge("mod.MyClass", "mod.other")
        graph.add_edge("mod.func", "mod.other")
        config = HealthCheckConfig(
            fan_out_max=1,
            fan_out_percentile=None,
        )
        result = check_fan_out(graph, config)
        entity_names = {f.entity_name for f in result.findings}
        self.assertNotIn("mod.MyClass", entity_names)
        self.assertIn("mod.func", entity_names)

    def test_fan_in_skips_classes(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.MyClass", "/f.py", 0, 100, node_type=Node.CLASS_TYPE))
        graph.add_node(_make_node("mod.func1", "/f.py", 0, 10, node_type=Node.FUNCTION_TYPE))
        graph.add_node(_make_node("mod.func2", "/f.py", 0, 10, node_type=Node.FUNCTION_TYPE))
        graph.add_edge("mod.func1", "mod.MyClass")
        graph.add_edge("mod.func2", "mod.MyClass")
        config = HealthCheckConfig(
            fan_in_max=1,
            fan_in_percentile=None,
        )
        result = check_fan_in(graph, config)
        entity_names = {f.entity_name for f in result.findings}
        self.assertNotIn("mod.MyClass", entity_names)

    def test_orphan_code_skips_classes_and_data(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.MyClass", "/f.py", 0, 100, node_type=Node.CLASS_TYPE))
        graph.add_node(_make_node("mod.MY_CONST", "/f.py", 0, 5, node_type=Node.CONSTANT_TYPE))
        graph.add_node(_make_node("mod.orphan_func", "/f.py", 0, 10, node_type=Node.FUNCTION_TYPE))
        result = check_orphan_code(graph)
        entity_names = {f.entity_name for f in result.findings}
        self.assertNotIn("mod.MyClass", entity_names)
        self.assertNotIn("mod.MY_CONST", entity_names)
        self.assertIn("mod.orphan_func", entity_names)
        self.assertEqual(result.total_entities_checked, 1)

    def test_entity_label_on_node(self):
        func_node = _make_node("mod.func", "/f.py", 0, 10, node_type=Node.FUNCTION_TYPE)
        method_node = _make_node("mod.Class.method", "/f.py", 0, 10, node_type=Node.METHOD_TYPE)
        class_node = _make_node("mod.MyClass", "/f.py", 0, 100, node_type=Node.CLASS_TYPE)
        prop_node = _make_node("mod.Class.prop", "/f.py", 0, 5, node_type=Node.PROPERTY_TYPE)
        const_node = _make_node("mod.CONST", "/f.py", 0, 5, node_type=Node.CONSTANT_TYPE)

        self.assertEqual(func_node.entity_label(), "Function")
        self.assertEqual(method_node.entity_label(), "Method")
        self.assertEqual(class_node.entity_label(), "Class")
        self.assertEqual(prop_node.entity_label(), "Property")
        self.assertEqual(const_node.entity_label(), "Constant")

    def test_node_type_predicates(self):
        func_node = _make_node("mod.func", "/f.py", 0, 10, node_type=Node.FUNCTION_TYPE)
        class_node = _make_node("mod.MyClass", "/f.py", 0, 100, node_type=Node.CLASS_TYPE)
        prop_node = _make_node("mod.prop", "/f.py", 0, 5, node_type=Node.PROPERTY_TYPE)

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
        self.assertEqual(config.function_size_max, 500)
        self.assertEqual(config.fan_out_max, 40)

    def test_custom_config(self):
        config = HealthCheckConfig(function_size_max=60)
        self.assertEqual(config.function_size_max, 60)


class TestInheritanceDepthFixedThreshold(unittest.TestCase):
    """Tests that inheritance depth uses a fixed threshold (no adaptive)."""

    def test_root_classes_not_flagged(self):
        """All root classes (depth 0) should not be flagged with default threshold."""
        hierarchy = {
            "ClassA": {
                "superclasses": [],
                "subclasses": [],
                "file_path": "/f.py",
                "line_start": 0,
                "line_end": 50,
            },
            "ClassB": {
                "superclasses": [],
                "subclasses": [],
                "file_path": "/f.py",
                "line_start": 50,
                "line_end": 100,
            },
        }
        config = HealthCheckConfig()  # default inheritance_depth_max=8
        result = check_inheritance_depth(hierarchy, config)
        self.assertEqual(result.findings_count, 0)
        self.assertEqual(result.score, 1.0)

    def test_fixed_threshold_ignores_distribution(self):
        """Even with percentile set, threshold should be fixed (percentile is None by default)."""
        hierarchy = {
            "Base": {
                "superclasses": [],
                "subclasses": [],
                "file_path": "/f.py",
                "line_start": 0,
                "line_end": 10,
            },
        }
        config = HealthCheckConfig(inheritance_depth_max=8)
        result = check_inheritance_depth(hierarchy, config)
        self.assertEqual(result.findings_count, 0)


class TestOrphanCodeCallbackFiltering(unittest.TestCase):
    """Tests that callbacks and anonymous functions are excluded from orphan code."""

    def test_callback_nodes_excluded(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.arr.find() callback", "/f.py", 10, 12))
        graph.add_node(_make_node("mod.arr.forEach() callback", "/f.py", 20, 22))
        graph.add_node(_make_node("mod.real_orphan", "/f.py", 30, 40))
        result = check_orphan_code(graph)
        entity_names = {f.entity_name for f in result.findings}
        self.assertNotIn("mod.arr.find() callback", entity_names)
        self.assertNotIn("mod.arr.forEach() callback", entity_names)
        self.assertIn("mod.real_orphan", entity_names)
        # Callbacks should be excluded from total_entities_checked
        self.assertEqual(result.total_entities_checked, 1)

    def test_anonymous_function_nodes_excluded(self):
        graph = CallGraph()
        graph.add_node(_make_node("mod.<function>", "/f.py", 5, 10))
        graph.add_node(_make_node("mod.<arrow", "/f.py", 15, 20))
        graph.add_node(_make_node("mod.normal_func", "/f.py", 25, 35))
        result = check_orphan_code(graph)
        entity_names = {f.entity_name for f in result.findings}
        self.assertNotIn("mod.<function>", entity_names)
        self.assertNotIn("mod.<arrow", entity_names)
        self.assertIn("mod.normal_func", entity_names)
        self.assertEqual(result.total_entities_checked, 1)


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

    def test_language_set_on_summary(self):
        from health.models import StandardCheckSummary

        summary = StandardCheckSummary(
            check_name="test",
            description="test check",
            total_entities_checked=0,
            findings_count=0,
            score=1.0,
            language="typescript",
        )
        self.assertEqual(summary.language, "typescript")

    def test_language_none_by_default(self):
        from health.models import StandardCheckSummary

        summary = StandardCheckSummary(
            check_name="test",
            description="test check",
            total_entities_checked=0,
            findings_count=0,
            score=1.0,
        )
        self.assertIsNone(summary.language)


if __name__ == "__main__":
    unittest.main()
