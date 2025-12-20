import unittest
from pathlib import Path

from agents.tools import CodeReferenceReader
from agents.tools.base import RepoContext
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer import StaticAnalyzer


class TestReadSourceTool(unittest.TestCase):
    def setUp(self):
        # Set up any necessary state or mocks before each test
        test_repo = Path("./test-vscode-repo")
        if not test_repo.exists():
            self.skipTest("Test repository not available")

        analyzer = StaticAnalyzer(test_repo)
        static_analysis = analyzer.analyze()
        ignore_manager = RepoIgnoreManager(test_repo)
        context = RepoContext(repo_dir=test_repo, ignore_manager=ignore_manager, static_analysis=static_analysis)
        self.tool = CodeReferenceReader(context=context)

        # Check if we have any references to work with
        if not static_analysis or len(static_analysis.get_all_source_files()) == 0:
            self.skipTest("No source files found for analysis")

    def test_read_method(self):
        # Test with an invalid reference since we don't have Python source files
        content = self.tool._run("some.method.reference")
        self.assertIsInstance(content, str)
        # Should get an error message
        self.assertIn("Error", content)

    def test_read_class(self):
        # Test with an invalid reference
        content = self.tool._run("some.class.reference")
        self.assertIsInstance(content, str)
        # Should get an error message
        self.assertIn("Error", content)

    def test_read_function(self):
        # Test with an invalid reference
        content = self.tool._run("some.function.reference")
        self.assertIsInstance(content, str)
        # Should get an error message
        self.assertIn("Error", content)

    def test_read_invalid_reference(self):
        # Test reading a file for a non-existing package
        error_msgs = self.tool._run("non_existing_package")
        self.assertIsInstance(error_msgs, str)
        # Should contain error information
        self.assertIn("Error", error_msgs)
