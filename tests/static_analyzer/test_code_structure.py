import unittest
from pathlib import Path

from agents.tools.read_structure import CodeStructureTool
from agents.tools.base import RepoContext
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_result import StaticAnalysisResults


class TestCodeStructureTool(unittest.TestCase):

    def setUp(self):
        # Create mock static analysis with class hierarchy
        self.static_analysis = StaticAnalysisResults()
        self.static_analysis.add_class_hierarchy(
            "python",
            {
                "myapp.models.User": {
                    "superclasses": ["BaseModel", "UserMixin"],
                    "subclasses": ["AdminUser", "GuestUser"],
                    "file_path": "myapp/models.py",
                    "line_start": 10,
                    "line_end": 50,
                },
                "myapp.models.BaseModel": {
                    "superclasses": [],
                    "subclasses": ["User", "Product"],
                    "file_path": "myapp/base.py",
                    "line_start": 5,
                    "line_end": 20,
                },
            },
        )
        ignore_manager = RepoIgnoreManager(Path("."))
        context = RepoContext(repo_dir=Path("."), ignore_manager=ignore_manager, static_analysis=self.static_analysis)
        self.tool = CodeStructureTool(context=context)

    def test_get_class_hierarchy(self):
        # Test retrieving class hierarchy
        result = self.tool._run("myapp.models.User")
        self.assertIn("myapp.models.User", result)
        self.assertIn("BaseModel", result)
        self.assertIn("UserMixin", result)
        self.assertIn("AdminUser", result)
        self.assertIn("GuestUser", result)

    def test_get_base_class(self):
        # Test retrieving base class with no superclasses
        result = self.tool._run("myapp.models.BaseModel")
        self.assertIn("myapp.models.BaseModel", result)
        self.assertIn("User", result)
        self.assertIn("Product", result)

    def test_class_not_found(self):
        # Test error handling for non-existent class
        result = self.tool._run("myapp.models.NonExistent")
        self.assertIn("No class hierarchy found", result)
        self.assertIn("myapp.models.NonExistent", result)
        self.assertIn("getSourceCode", result)

    def test_multiple_languages(self):
        # Test with multiple languages
        self.static_analysis.add_class_hierarchy(
            "typescript",
            {
                "src.controllers.UserController": {
                    "superclasses": ["BaseController"],
                    "subclasses": [],
                    "file_path": "src/controllers.ts",
                    "line_start": 15,
                    "line_end": 45,
                }
            },
        )

        result = self.tool._run("src.controllers.UserController")
        self.assertIn("UserController", result)
        self.assertIn("BaseController", result)

    def test_no_static_analysis(self):
        # Test error when static analysis is None
        context = RepoContext(repo_dir=Path("."), ignore_manager=RepoIgnoreManager(Path(".")), static_analysis=None)
        tool = CodeStructureTool(context=context)
        result = tool._run("myapp.models.User")
        self.assertIn("Error: Static analysis is not set", result)

    def test_case_sensitivity(self):
        # Test that qualified names are case-sensitive
        result = self.tool._run("myapp.models.user")
        self.assertIn("No class hierarchy found", result)
