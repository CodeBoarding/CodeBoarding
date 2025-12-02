import unittest
from pathlib import Path

from agents.tools import GetCFGTool, MethodInvocationsTool
from static_analyzer import StaticAnalyzer


class TestCFGTools(unittest.TestCase):
    def setUp(self):
        # Set up any necessary state or mocks before each test
        analyzer = StaticAnalyzer(Path("./temp/test"))
        static_analysis = analyzer.analyze()
        self.read_cfg = GetCFGTool(static_analysis)
        self.method_tool = MethodInvocationsTool(static_analysis)

    def test_get_cfg(self):
        # Test the _run method with a valid function
        content = self.read_cfg._run()
        self.assertIn("is calling", content)

    def test_method_cfg(self):
        # Test the _run method with a valid function
        content = self.method_tool._run("django.docs._ext.github_links.CodeLocator.from_code")
        self.assertIn("is calling", content)
        self.assertIn("called by", content)
        self.assertIn("ast.parse", content)
        self.assertIn("django.docs._ext.github_links.CodeLocator.visit_ImportFrom", content)
