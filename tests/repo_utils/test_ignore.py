import tempfile
import unittest
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager


class TestRepoIgnoreManager(unittest.TestCase):
    """Tests for the RepoIgnoreManager class."""

    def setUp(self):
        """Create a temporary repository for testing."""
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir)
        self.ignore_manager = RepoIgnoreManager(self.repo_path)

    def test_ignore_default_directories(self):
        """Test that default directories are ignored."""
        test_dirs = [
            Path("node_modules"),
            Path(".git"),
            Path("__pycache__"),
            Path("build"),
            Path("dist"),
            Path(".next"),
            Path(".venv"),
            Path("venv"),
            Path("env"),
        ]

        for dir_path in test_dirs:
            with self.subTest(dir_path=dir_path):
                self.assertTrue(
                    self.ignore_manager.should_ignore(dir_path),
                    f"{dir_path} should be ignored",
                )

    def test_ignore_bundle_files(self):
        """Test that bundle files are ignored."""
        bundle_files = [
            Path("src/app.bundle.js"),
            Path("dist/main.bundle.js"),
            Path("public/vendor.bundle.js"),
        ]

        for file_path in bundle_files:
            with self.subTest(file_path=file_path):
                self.assertTrue(
                    self.ignore_manager.should_ignore(file_path),
                    f"{file_path} should be ignored",
                )

    def test_ignore_bundle_source_maps(self):
        """Test that bundle source maps are ignored."""
        source_map_files = [
            Path("src/app.bundle.js.map"),
            Path("dist/main.bundle.js.map"),
        ]

        for file_path in source_map_files:
            with self.subTest(file_path=file_path):
                self.assertTrue(
                    self.ignore_manager.should_ignore(file_path),
                    f"{file_path} should be ignored",
                )

    def test_ignore_minified_js_files(self):
        """Test that minified JavaScript files are ignored."""
        minified_js_files = [
            Path("src/app.min.js"),
            Path("dist/vendor.min.js"),
            Path("public/lib.min.js"),
        ]

        for file_path in minified_js_files:
            with self.subTest(file_path=file_path):
                self.assertTrue(
                    self.ignore_manager.should_ignore(file_path),
                    f"{file_path} should be ignored",
                )

    def test_ignore_minified_css_files(self):
        """Test that minified CSS files are ignored."""
        minified_css_files = [
            Path("src/styles.min.css"),
            Path("dist/main.min.css"),
            Path("public/theme.min.css"),
        ]

        for file_path in minified_css_files:
            with self.subTest(file_path=file_path):
                self.assertTrue(
                    self.ignore_manager.should_ignore(file_path),
                    f"{file_path} should be ignored",
                )

    def test_ignore_chunk_files(self):
        """Test that chunk files are ignored."""
        chunk_files = [
            Path("dist/0.chunk.js"),
            Path("dist/123.chunk.js"),
            Path("dist/vendors~main.chunk.js"),
        ]

        for file_path in chunk_files:
            with self.subTest(file_path=file_path):
                self.assertTrue(
                    self.ignore_manager.should_ignore(file_path),
                    f"{file_path} should be ignored",
                )

    def test_ignore_chunk_source_maps(self):
        """Test that chunk source maps are ignored."""
        chunk_map_files = [
            Path("dist/0.chunk.js.map"),
            Path("dist/123.chunk.js.map"),
        ]

        for file_path in chunk_map_files:
            with self.subTest(file_path=file_path):
                self.assertTrue(
                    self.ignore_manager.should_ignore(file_path),
                    f"{file_path} should be ignored",
                )

    def test_include_normal_source_files(self):
        """Test that normal source files are not ignored."""
        normal_files = [
            Path("src/app.js"),
            Path("src/components/Button.jsx"),
            Path("src/styles.css"),
            Path("src/utils/helper.ts"),
            Path("src/index.html"),
        ]

        for file_path in normal_files:
            with self.subTest(file_path=file_path):
                self.assertFalse(
                    self.ignore_manager.should_ignore(file_path),
                    f"{file_path} should NOT be ignored",
                )

    def test_include_normal_directories(self):
        """Test that normal directories are not ignored."""
        normal_dirs = [
            Path("src"),
            Path("components"),
            Path("utils"),
            Path("public"),
        ]

        for dir_path in normal_dirs:
            with self.subTest(dir_path=dir_path):
                self.assertFalse(
                    self.ignore_manager.should_ignore(dir_path),
                    f"{dir_path} should NOT be ignored",
                )

    def test_filter_paths(self):
        """Test filtering a list of paths."""
        paths = [
            Path("src/app.js"),
            Path("src/app.bundle.js"),
            Path("node_modules/react"),
            Path("src/styles.css"),
            Path("dist/main.min.js"),
        ]

        filtered = self.ignore_manager.filter_paths(paths)

        # Should only include src/app.js and src/styles.css
        self.assertEqual(len(filtered), 2)
        self.assertIn(Path("src/app.js"), filtered)
        self.assertIn(Path("src/styles.css"), filtered)
        self.assertNotIn(Path("src/app.bundle.js"), filtered)
        self.assertNotIn(Path("node_modules/react"), filtered)
        self.assertNotIn(Path("dist/main.min.js"), filtered)

    def test_hidden_directories_ignored(self):
        """Test that hidden directories (starting with .) are ignored."""
        hidden_dirs = [
            Path(".cache"),
            Path(".vscode"),
            Path(".idea"),
        ]

        for dir_path in hidden_dirs:
            with self.subTest(dir_path=dir_path):
                self.assertTrue(
                    self.ignore_manager.should_ignore(dir_path),
                    f"{dir_path} should be ignored",
                )


if __name__ == "__main__":
    unittest.main()
