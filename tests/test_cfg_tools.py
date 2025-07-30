import unittest

from agents.tools import GetCFGTool, MethodInvocationsTool
from agents.tools.utils import read_dot_file


class TestCFGTools(unittest.TestCase):
    def setUp(self):
        # Set up any necessary state or mocks before each test
        cfg = read_dot_file("./temp/test/call_graph.dot")
        self.read_cfg = GetCFGTool(cfg)
        self.method_tool = MethodInvocationsTool(cfg)

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
