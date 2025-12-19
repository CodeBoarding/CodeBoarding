import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agents.agent_responses import Component
from agents.tools import GetCFGTool, MethodInvocationsTool
from agents.tools.base import RepoContext
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer import StaticAnalyzer


class TestCFGTools(unittest.TestCase):
    def setUp(self):
        # Set up any necessary state or mocks before each test
        test_repo = Path("./test-vscode-repo")
        if not test_repo.exists():
            self.skipTest("Test repository not available")
        analyzer = StaticAnalyzer(test_repo)
        static_analysis = analyzer.analyze()
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
            referenced_source_code=[],
            assigned_files=[],
        )

        # Get some files from the analysis
        for lang in self.static_analysis.get_languages():
            cfg = self.static_analysis.get_cfg(lang)
            if cfg and cfg.nodes:
                # Add first node's file to component
                first_node = next(iter(cfg.nodes.values()))
                component.assigned_files.append(first_node.file_path)
                break

        result = self.read_cfg.component_cfg(component)
        self.assertIsInstance(result, str)
        self.assertIn(component.name, result)

    def test_component_cfg_without_static_analysis(self):
        # Test when static_analysis is None
        context = RepoContext(repo_dir=Path("."), ignore_manager=MagicMock(), static_analysis=None)
        tool = GetCFGTool(context=context)
        component = Component(name="Test", description="Test component", referenced_source_code=[], assigned_files=[])
        result = tool.component_cfg(component)
        self.assertEqual(result, "No static analysis data available.")

    def test_component_cfg_with_no_matching_files(self):
        # Test component with files that don't exist in CFG
        component = Component(
            name="EmptyComponent",
            description="Empty test component",
            referenced_source_code=[],
            assigned_files=["nonexistent.py"],
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
