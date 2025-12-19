import unittest
from pathlib import Path

from agents.tools.read_packages import PackageRelationsTool
from agents.tools.base import RepoContext
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_result import StaticAnalysisResults


class TestPackageRelationsTool(unittest.TestCase):

    def setUp(self):
        # Create mock static analysis with package dependencies
        self.static_analysis = StaticAnalysisResults()
        self.static_analysis.add_package_dependencies(
            "python",
            {
                "mypackage": {"imports": ["requests", "flask"], "imported_by": ["main"]},
                "utils": {"imports": ["json", "os"], "imported_by": ["mypackage", "tests"]},
            },
        )
        ignore_manager = RepoIgnoreManager(Path("."))
        context = RepoContext(repo_dir=Path("."), ignore_manager=ignore_manager, static_analysis=self.static_analysis)
        self.tool = PackageRelationsTool(context=context)

    def test_get_package_dependencies(self):
        # Test retrieving package dependencies
        result = self.tool._run("mypackage")
        self.assertIn("mypackage", result)
        self.assertIn("requests", result)
        self.assertIn("flask", result)
        self.assertIn("main", result)

    def test_get_utils_package(self):
        # Test retrieving dependencies for utils package
        result = self.tool._run("utils")
        self.assertIn("utils", result)
        self.assertIn("json", result)
        self.assertIn("os", result)
        self.assertIn("mypackage", result)
        self.assertIn("tests", result)

    def test_package_not_found(self):
        # Test error handling for non-existent package
        result = self.tool._run("nonexistent")
        self.assertIn("No package relations found", result)
        self.assertIn("nonexistent", result)

    def test_multiple_languages(self):
        # Test with multiple languages
        self.static_analysis.add_package_dependencies(
            "typescript",
            {
                "src": {"imports": ["express", "axios"], "imported_by": []},
            },
        )

        # Should find in TypeScript
        result = self.tool._run("src")
        self.assertIn("express", result)
        self.assertIn("axios", result)

    def test_no_static_analyzer(self):
        # Test error when static analyzer is None
        context = RepoContext(repo_dir=Path("."), ignore_manager=RepoIgnoreManager(Path(".")), static_analysis=None)
        tool = PackageRelationsTool(context=context)
        result = tool._run("mypackage")
        self.assertIn("Error: Static analysis is not set", result)

    def test_package_list_in_error(self):
        # Test that error message includes available packages
        result = self.tool._run("badpackage")
        self.assertIn("No package relations found", result)
        # Should show available packages from the results
        self.assertIn("mypackage", result)
