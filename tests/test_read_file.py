import unittest
from pathlib import Path

from agents.tools.read_file import ReadFileTool


class TestReadFileTool(unittest.TestCase):

    def setUp(self):
        # Set up any necessary state or mocks before each test
        self.tool = ReadFileTool(repo_dir=Path("./repos/django"))

    def test_read_file(self):
        # Test the _run method with a valid file path
        content = self.tool._run("django/db/migrations/autodetector.py", 100)
        self.assertIsInstance(content, str)
        self.assertIn("100:                # we have a field which also returns a name", content)
        self.assertIn("23:class OperationDependency(", content)
        self.assertIn("199:        # generate_altered_index/unique_together().", content)

    def test_read_bad_file(self):
        # Test the _run method with a valid file path
        content = self.tool._run("badfile", 100)
        self.assertIsInstance(content, str)
        self.assertIn("Error: The specified file 'badfile' was not found in the indexed source files", content)
