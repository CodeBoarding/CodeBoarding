import tempfile
import unittest
import shutil
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager


class TestRepoIgnoreManagerRealWorldScenario(unittest.TestCase):
    """End-to-end tests verifying ignore patterns work with actual files."""

    def setUp(self):
        """Create a temporary repository with actual files and ignore configurations."""
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir)
        self._create_test_repo_structure()
        self.ignore_manager = RepoIgnoreManager(self.repo_path)

    def tearDown(self):
        """Clean up the temporary repository."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_test_repo_structure(self):
        """Create a realistic repository structure with files to be analyzed."""
        # Create directories
        (self.repo_path / "src").mkdir()
        (self.repo_path / "src" / "components").mkdir()
        (self.repo_path / "spec").mkdir()
        (self.repo_path / "dist").mkdir()
        (self.repo_path / "node_modules").mkdir()
        (self.repo_path / ".codeboarding").mkdir()

        # Create actual files that should be analyzed
        (self.repo_path / "src" / "main.py").write_text("# Main app code")
        (self.repo_path / "src" / "utils.py").write_text("# Utils")
        (self.repo_path / "src" / "components" / "button.ts").write_text("// Button component")
        (self.repo_path / "spec" / "main_spec.py").write_text("# Spec file")

        # Create files that should be ignored (generated/build artifacts)
        (self.repo_path / "dist" / "bundle.js").write_text("// Bundled code")
        (self.repo_path / "dist" / "app.min.js").write_text("// Minified code")
        (self.repo_path / "dist" / "app.bundle.js.map").write_text("// Source map")
        (self.repo_path / "node_modules" / "react").mkdir()
        (self.repo_path / "node_modules" / "react" / "index.js").write_text("// React library")

        # Create .gitignore with patterns
        gitignore_content = """*.log
*.tmp
generated/
temp_files/
"""
        (self.repo_path / ".gitignore").write_text(gitignore_content)

        # Create .codeboardingignore with patterns
        codeboardingignore_content = """# CodeBoarding Ignore File
vendor/
third_party/
*.backup
"""
        (self.repo_path / ".codeboarding" / ".codeboardingignore").write_text(codeboardingignore_content)

        # Create files matching gitignore patterns
        (self.repo_path / "build.log").write_text("Build log content")
        (self.repo_path / "cache.tmp").write_text("Temp cache")
        (self.repo_path / "generated").mkdir()
        (self.repo_path / "generated" / "code.py").write_text("# Generated")

        # Create files matching codeboardingignore patterns
        (self.repo_path / "vendor").mkdir()
        (self.repo_path / "vendor" / "library.py").write_text("# Vendor code")
        (self.repo_path / "app.backup").write_text("# Backup")

    def test_gitignore_patterns_are_applied(self):
        """Verify that .gitignore patterns are respected."""
        # Files matching gitignore patterns should be ignored
        self.assertTrue(self.ignore_manager.should_ignore(Path("build.log")))
        self.assertTrue(self.ignore_manager.should_ignore(Path("cache.tmp")))
        self.assertTrue(self.ignore_manager.should_ignore(Path("generated/code.py")))

    def test_codeboardingignore_patterns_are_applied(self):
        """Verify that .codeboardingignore patterns are respected."""
        # Files matching codeboardingignore patterns should be ignored
        self.assertTrue(self.ignore_manager.should_ignore(Path("vendor/library.py")))
        self.assertTrue(self.ignore_manager.should_ignore(Path("app.backup")))

    def test_default_ignored_directories_are_applied(self):
        """Verify that default ignored directories and file patterns are excluded."""
        # Default ignored directories
        self.assertTrue(self.ignore_manager.should_ignore(Path("node_modules/react/index.js")))
        self.assertTrue(self.ignore_manager.should_ignore(Path("dist/bundle.js")))
        self.assertTrue(self.ignore_manager.should_ignore(Path(".codeboarding/config.json")))

        # Default ignored file patterns (build artifacts, minified files)
        self.assertTrue(self.ignore_manager.should_ignore(Path("src/app.bundle.js")))
        self.assertTrue(self.ignore_manager.should_ignore(Path("src/app.bundle.js.map")))
        self.assertTrue(self.ignore_manager.should_ignore(Path("src/app.min.js")))
        self.assertTrue(self.ignore_manager.should_ignore(Path("src/styles.min.css")))
        self.assertTrue(self.ignore_manager.should_ignore(Path("dist/0.chunk.js")))
        self.assertTrue(self.ignore_manager.should_ignore(Path("dist/0.chunk.js.map")))

    def test_source_files_are_not_ignored(self):
        """Verify that actual source files are not ignored."""
        # Source code files in various locations
        self.assertFalse(self.ignore_manager.should_ignore(Path("src/main.py")))
        self.assertFalse(self.ignore_manager.should_ignore(Path("src/utils.py")))
        self.assertFalse(self.ignore_manager.should_ignore(Path("src/components/button.ts")))
        self.assertFalse(self.ignore_manager.should_ignore(Path("spec/main_spec.py")))

        # Normal directories and files
        self.assertFalse(self.ignore_manager.should_ignore(Path("src")))
        self.assertFalse(self.ignore_manager.should_ignore(Path("src/app.js")))
        self.assertFalse(self.ignore_manager.should_ignore(Path("src/styles.css")))

    def test_hidden_directories_are_ignored(self):
        """Verify that hidden directories (starting with .) are ignored."""
        self.assertTrue(self.ignore_manager.should_ignore(Path(".cache")))
        self.assertTrue(self.ignore_manager.should_ignore(Path(".vscode")))
        self.assertTrue(self.ignore_manager.should_ignore(Path(".idea")))

    def test_filter_paths_with_mixed_files(self):
        """Verify that filtering separates source files from ignored files."""
        paths = [
            # Source files
            Path("src/main.py"),
            Path("src/components/button.ts"),
            Path("spec/main_spec.py"),
            # Files to be ignored
            Path("build.log"),
            Path("vendor/library.py"),
            Path("node_modules/react/index.js"),
            Path("dist/app.min.js"),
            Path("app.backup"),
            Path("src/app.bundle.js"),
        ]

        filtered = self.ignore_manager.filter_paths(paths)

        # Only source files should be included (3 files)
        self.assertEqual(len(filtered), 3)
        self.assertIn(Path("src/main.py"), filtered)
        self.assertIn(Path("src/components/button.ts"), filtered)
        self.assertIn(Path("spec/main_spec.py"), filtered)

        # All ignored patterns should be excluded
        for ignored_path in [
            Path("build.log"),
            Path("vendor/library.py"),
            Path("node_modules/react/index.js"),
            Path("dist/app.min.js"),
            Path("app.backup"),
            Path("src/app.bundle.js"),
        ]:
            self.assertNotIn(ignored_path, filtered, f"{ignored_path} should be filtered out")

    def test_nested_ignored_directories_are_excluded(self):
        """Verify that files inside ignored directories are also excluded."""
        self.assertTrue(self.ignore_manager.should_ignore(Path("generated/code.py")))
        self.assertTrue(self.ignore_manager.should_ignore(Path("vendor/library.py")))
        self.assertTrue(self.ignore_manager.should_ignore(Path("node_modules/react/index.js")))


if __name__ == "__main__":
    unittest.main()
