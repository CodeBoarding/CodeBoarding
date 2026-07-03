import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agents.agent_responses import ClusterAnalysis, ClustersComponent, Component, FileMethodGroup
from agents.tools import ComponentBridgeEdgesTool, GetCFGTool, MethodInvocationsTool
from agents.tools.base import RepoContext
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer import StaticAnalyzer
from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph, ClusterResult, Edge
from static_analyzer.node import Node
from utils import get_artifact_dir


class TestCFGTools(unittest.TestCase):
    def setUp(self):
        # Set up any necessary state or mocks before each test
        test_repo = Path("./test-vscode-repo")
        if not test_repo.exists():
            self.skipTest("Test repository not available")
        analyzer = StaticAnalyzer(test_repo)
        static_analysis = analyzer.analyze(cache_dir=get_artifact_dir(test_repo))
        ignore_manager = RepoIgnoreManager(test_repo)
        self.context = RepoContext(repo_dir=test_repo, ignore_manager=ignore_manager, static_analysis=static_analysis)
        self.read_cfg = GetCFGTool(context=self.context)
        self.method_tool = MethodInvocationsTool(context=self.context)
        self.static_analysis = static_analysis

    def test_get_cfg_with_valid_data(self):
        # Test the _run method with a valid function
        content = self.read_cfg._run()
        # Check that we get some CFG output
        self.assertIsInstance(content, str)
        self.assertTrue(len(content) > 0)
        self.assertNotIn("No control flow graph data available", content)

    def test_get_cfg_without_static_analysis(self):
        # Test when static_analysis is None
        context = RepoContext(repo_dir=Path("."), ignore_manager=MagicMock(), static_analysis=None)
        tool = GetCFGTool(context=context)
        result = tool._run()
        self.assertEqual(result, "No static analysis data available.")

    def test_get_cfg_with_empty_cfg(self):
        # Test when CFG is empty or has no data
        mock_analysis = MagicMock()
        mock_analysis.get_languages.return_value = ["python"]
        mock_analysis.get_cfg.return_value = None

        context = RepoContext(repo_dir=Path("."), ignore_manager=MagicMock(), static_analysis=mock_analysis)
        tool = GetCFGTool(context=context)
        result = tool._run()
        self.assertIn("No control flow graph data available", result)

    def test_component_cfg_with_valid_component(self):
        # Create a component with some files from the static analysis
        component = Component(
            name="TestComponent",
            description="Test component for CFG testing",
            key_entities=[],
        )

        # Get some files from the analysis
        for lang in self.static_analysis.get_languages():
            cfg = self.static_analysis.get_cfg(lang)
            if cfg and cfg.nodes:
                # Add first node's file to component
                first_node = next(iter(cfg.nodes.values()))
                component.file_methods.append(FileMethodGroup(file_path=first_node.file_path))
                break

        result = self.read_cfg.component_cfg(component)
        self.assertIsInstance(result, str)
        self.assertIn(component.name, result)

    def test_component_cfg_without_static_analysis(self):
        # Test when static_analysis is None
        context = RepoContext(repo_dir=Path("."), ignore_manager=MagicMock(), static_analysis=None)
        tool = GetCFGTool(context=context)
        component = Component(name="Test", description="Test component", key_entities=[])
        result = tool.component_cfg(component)
        self.assertEqual(result, "No static analysis data available.")

    def test_component_cfg_with_no_matching_files(self):
        # Test component with files that don't exist in CFG
        component = Component(
            name="EmptyComponent",
            description="Empty test component",
            key_entities=[],
            file_methods=[FileMethodGroup(file_path="nonexistent.py")],
        )
        result = self.read_cfg.component_cfg(component)
        self.assertIn("No control flow graph data available for this component", result)

    def test_method_invocations_with_valid_method(self):
        # Find a method that exists in the CFG
        method_name = None
        for lang in self.static_analysis.get_languages():
            cfg = self.static_analysis.get_cfg(lang)
            if cfg and cfg.edges:
                # Get the first edge's source node
                first_edge = next(iter(cfg.edges))
                method_name = first_edge.src_node.fully_qualified_name
                break

        if method_name:
            content = self.method_tool._run(method_name)
            self.assertIsInstance(content, str)
            self.assertTrue(len(content) > 0)

    def test_method_invocations_with_nonexistent_method(self):
        # Test with a method that doesn't exist
        content = self.method_tool._run("nonexistent.method.name")
        self.assertIn("No method invocations found", content)

    def test_method_invocations_without_static_analysis(self):
        # Test when static_analysis is None
        context = RepoContext(repo_dir=Path("."), ignore_manager=MagicMock(), static_analysis=None)
        tool = MethodInvocationsTool(context=context)
        result = tool._run("some.method")
        self.assertEqual(result, "No static analysis data available.")

    def test_method_invocations_as_callee(self):
        # Test finding methods that are called by others
        method_name = None
        for lang in self.static_analysis.get_languages():
            cfg = self.static_analysis.get_cfg(lang)
            if cfg and cfg.edges:
                # Get a method that is called (destination node)
                first_edge = next(iter(cfg.edges))
                method_name = first_edge.dst_node.fully_qualified_name
                break

        if method_name:
            content = self.method_tool._run(method_name)
            self.assertIsInstance(content, str)
            # Should contain "is called by" somewhere
            if "No method invocations found" not in content:
                self.assertTrue("is calling" in content or "is called by" in content)


class TestComponentBridgeEdgesTool(unittest.TestCase):
    def _make_tool(self) -> ComponentBridgeEdgesTool:
        src = Node("pkg.source.call", NodeType.FUNCTION, "src.py", 10, 12)
        dst = Node("pkg.destination.handle", NodeType.FUNCTION, "dst.py", 20, 22)
        other = Node("pkg.other.handle", NodeType.FUNCTION, "other.py", 30, 32)
        cfg = CallGraph(
            nodes={
                src.fully_qualified_name: src,
                dst.fully_qualified_name: dst,
                other.fully_qualified_name: other,
            },
            edges=[Edge(src, dst), Edge(dst, other)],
        )
        cluster_results = {
            "python": ClusterResult(
                clusters={1: {src.fully_qualified_name}, 2: {dst.fully_qualified_name}, 3: {other.fully_qualified_name}}
            )
        }
        cluster_analysis = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(name="Source Group", cluster_ids=[1], description="source"),
                ClustersComponent(name="Destination Group", cluster_ids=[2], description="destination"),
                ClustersComponent(name="Other Group", cluster_ids=[3], description="other"),
            ]
        )
        context = RepoContext(
            repo_dir=Path("."),
            ignore_manager=RepoIgnoreManager(Path(".")),
            cluster_analysis=cluster_analysis,
            cluster_results=cluster_results,
            cfg_graphs={"python": cfg},
        )
        return ComponentBridgeEdgesTool(context=context)

    def test_returns_directed_edges_between_component_groups(self):
        tool = self._make_tool()
        result = tool._run(["Source Group"], ["Destination Group"])

        self.assertIn("Directed static bridge edges (1)", result)
        self.assertIn("pkg.source.call", result)
        self.assertIn("pkg.destination.handle", result)
        self.assertIn("src.py:10", result)

    def test_reverse_direction_is_not_reported(self):
        tool = self._make_tool()
        result = tool._run(["Destination Group"], ["Source Group"])

        self.assertEqual(result, "No directed static bridge edges found between these component groups.")
