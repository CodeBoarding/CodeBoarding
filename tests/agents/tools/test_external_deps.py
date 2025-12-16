import unittest
from pathlib import Path
import tempfile
import shutil

from agents.tools.external_deps import ExternalDepsTool


class TestExternalDepsTool(unittest.TestCase):

    def setUp(self):
        # Create a temporary directory for testing
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        # Clean up the temporary directory
        shutil.rmtree(self.temp_dir)

    def test_find_python_deps(self):
        # Create Python dependency files
        (self.temp_dir / "requirements.txt").touch()
        (self.temp_dir / "setup.py").touch()

        tool = ExternalDepsTool(repo_dir=self.temp_dir)
        result = tool._run()

        self.assertIn("Found 2 dependency file(s)", result)
        self.assertIn("requirements.txt", result)
        self.assertIn("setup.py", result)

    def test_find_nodejs_deps(self):
        # Create Node.js dependency files
        (self.temp_dir / "package.json").touch()
        (self.temp_dir / "package-lock.json").touch()

        tool = ExternalDepsTool(repo_dir=self.temp_dir)
        result = tool._run()

        self.assertIn("Found 2 dependency file(s)", result)
        self.assertIn("package.json", result)
        self.assertIn("package-lock.json", result)

    def test_find_in_subdirectories(self):
        # Create subdirectory with requirements
        req_dir = self.temp_dir / "requirements"
        req_dir.mkdir()
        (req_dir / "dev.txt").touch()
        (req_dir / "test.txt").touch()

        tool = ExternalDepsTool(repo_dir=self.temp_dir)
        result = tool._run()

        self.assertIn("Found", result)
        self.assertIn("requirements", result)

    def test_no_deps_found(self):
        # Test with empty directory
        tool = ExternalDepsTool(repo_dir=self.temp_dir)
        result = tool._run()

        self.assertIn("No dependency files found", result)
        self.assertIn("requirements.txt", result)
        self.assertIn("pyproject.toml", result)

    def test_mixed_dependency_files(self):
        # Create various dependency files
        (self.temp_dir / "requirements.txt").touch()
        (self.temp_dir / "package.json").touch()
        (self.temp_dir / "Pipfile").touch()
        (self.temp_dir / "environment.yml").touch()

        tool = ExternalDepsTool(repo_dir=self.temp_dir)
        result = tool._run()

        self.assertIn("Found 4 dependency file(s)", result)
        self.assertIn("requirements.txt", result)
        self.assertIn("package.json", result)
        self.assertIn("Pipfile", result)
        self.assertIn("environment.yml", result)

    def test_readfile_suggestion(self):
        # Test that output includes readFile tool usage suggestion
        (self.temp_dir / "requirements.txt").touch()

        tool = ExternalDepsTool(repo_dir=self.temp_dir)
        result = tool._run()

        self.assertIn("To read this file:", result)
        self.assertIn("readFile tool", result)
