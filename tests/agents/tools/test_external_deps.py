import unittest
from pathlib import Path
import tempfile
import shutil

from agents.tools.get_external_deps import ExternalDepsTool
from agents.tools.base import RepoContext
from repo_utils.ignore import RepoIgnoreManager


class TestExternalDepsTool(unittest.TestCase):

    def setUp(self):
        # Create a temporary directory for testing
        self.temp_dir = Path(tempfile.mkdtemp())
        self.ignore_manager = RepoIgnoreManager(self.temp_dir)
        self.context = RepoContext(repo_dir=self.temp_dir, ignore_manager=self.ignore_manager)

    def tearDown(self):
        # Clean up the temporary directory
        shutil.rmtree(self.temp_dir)

    def test_find_python_deps(self):
        # Create Python dependency files
        (self.temp_dir / "requirements.txt").touch()
        (self.temp_dir / "setup.py").touch()

        tool = ExternalDepsTool(context=self.context)
        result = tool._run()

        self.assertIn("Found 2 dependency file(s)", result)
        self.assertIn("requirements.txt", result)
        self.assertIn("setup.py", result)

    def test_find_nodejs_deps(self):
        # Create Node.js dependency files
        (self.temp_dir / "package.json").touch()
        (self.temp_dir / "package-lock.json").touch()

        tool = ExternalDepsTool(context=self.context)
        result = tool._run()

        self.assertIn("Found 2 dependency file(s)", result)
        self.assertIn("package.json", result)
        self.assertIn("package-lock.json", result)

    def test_find_in_subdirectories(self):
        sub = self.temp_dir / "backend"
        sub.mkdir()
        (sub / "requirements.txt").touch()
        (sub / "pyproject.toml").touch()

        tool = ExternalDepsTool(context=self.context)
        result = tool._run()

        self.assertIn("Found 2 dependency file(s)", result)
        self.assertIn("requirements.txt", result)
        self.assertIn("pyproject.toml", result)

    def test_no_deps_found(self):
        # Test with empty directory
        tool = ExternalDepsTool(context=self.context)
        result = tool._run()

        self.assertIn("No dependency files found", result)
        self.assertIn("requirements.txt", result)
        self.assertIn("pyproject.toml", result)

    def test_find_go_deps(self):
        (self.temp_dir / "go.mod").touch()
        (self.temp_dir / "go.sum").touch()

        tool = ExternalDepsTool(context=self.context)
        result = tool._run()

        self.assertIn("Found 2 dependency file(s)", result)
        self.assertIn("go.mod", result)
        self.assertIn("go.sum", result)

    def test_find_java_deps(self):
        (self.temp_dir / "pom.xml").touch()
        (self.temp_dir / "gradle.properties").touch()

        tool = ExternalDepsTool(context=self.context)
        result = tool._run()

        self.assertIn("Found 2 dependency file(s)", result)
        self.assertIn("pom.xml", result)
        self.assertIn("gradle.properties", result)

    def test_find_php_deps(self):
        (self.temp_dir / "composer.json").touch()
        (self.temp_dir / "composer.lock").touch()

        tool = ExternalDepsTool(context=self.context)
        result = tool._run()

        self.assertIn("Found 2 dependency file(s)", result)
        self.assertIn("composer.json", result)
        self.assertIn("composer.lock", result)

    def test_ignore_unsupported_rust_deps(self):
        (self.temp_dir / "Cargo.toml").touch()
        (self.temp_dir / "Cargo.lock").touch()

        tool = ExternalDepsTool(context=self.context)
        result = tool._run()

        self.assertIn("No dependency files found", result)

    def test_mixed_dependency_files(self):
        # Create various dependency files
        (self.temp_dir / "requirements.txt").touch()
        (self.temp_dir / "package.json").touch()
        (self.temp_dir / "Pipfile").touch()
        (self.temp_dir / "environment.yml").touch()

        tool = ExternalDepsTool(context=self.context)
        result = tool._run()

        self.assertIn("Found 4 dependency file(s)", result)
        self.assertIn("requirements.txt", result)
        self.assertIn("package.json", result)
        self.assertIn("Pipfile", result)
        self.assertIn("environment.yml", result)

    def test_readfile_suggestion(self):
        # Test that output includes readFile tool usage suggestion
        (self.temp_dir / "requirements.txt").touch()

        tool = ExternalDepsTool(context=self.context)
        result = tool._run()

        self.assertIn("To read this file:", result)
        self.assertIn("readFile tool", result)
