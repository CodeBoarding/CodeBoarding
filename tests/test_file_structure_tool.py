import unittest
from pathlib import Path

from agents.tools import FileStructureTool


class TestFileStructureTool(unittest.TestCase):
    def setUp(self):
        # Set up any necessary state or mocks before each test
        self.tool = FileStructureTool(analysis_dir=Path("/home/ivan/StartUp/CodeBoarding/repos/django"))

    def test_file_structure(self):
        # Test the _run method with a valid directory
        content = self.tool._run("django")
        self.assertIn("The file tree for django is", content)
        self.assertIn("images.py", content)

    def test_file_structure_sub_module(self):
        content = self.tool._run("django/core/files")
        self.assertIn("The file tree for django/core/files is", content)
        self.assertIn("images.py", content)
        self.assertIn("storage", content)
        self.assertIn("memory.py", content)

    def test_invalid_directory(self):
        # Test reading a file for a non-existing directory
        content = self.tool._run("non_existing_directory")
        self.assertIn("Error: The specified directory does not exist or is empty. Available", content)
