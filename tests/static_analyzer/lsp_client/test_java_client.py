"""
Tests for Java LSP client.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, call

from static_analyzer.lsp_client.java_client import JavaClient
from static_analyzer.java_config_scanner import JavaProjectConfig
from static_analyzer.programming_language import ProgrammingLanguage
from repo_utils.ignore import RepoIgnoreManager


class TestJavaClient(unittest.TestCase):
    """Test JavaClient class."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.project_path = Path(self.temp_dir)

        # Create mock language
        self.mock_language = Mock(spec=ProgrammingLanguage)
        self.mock_language.get_server_parameters.return_value = ["java", "-jar", "jdtls.jar"]
        self.mock_language.get_suffix_pattern.return_value = ["*.java"]
        self.mock_language.get_language_id.return_value = "java"
        self.mock_language.config_extra = {}

        # Create mock ignore manager
        self.mock_ignore_manager = Mock(spec=RepoIgnoreManager)
        self.mock_ignore_manager.should_ignore.return_value = False

        # Create project config
        self.project_config = JavaProjectConfig(self.project_path, "maven", False)

    def tearDown(self):
        """Clean up test directory."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init(self):
        """Test JavaClient initialization."""
        jdtls_root = Path("/opt/jdtls")

        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
            jdtls_root,
        )

        self.assertEqual(client.project_config, self.project_config)
        self.assertIsNone(client.workspace_dir)
        self.assertTrue(client.temp_workspace)
        self.assertEqual(client.jdtls_root, jdtls_root)
        self.assertFalse(client.import_complete)
        self.assertEqual(len(client.import_errors), 0)

    def test_init_without_jdtls_root(self):
        """Test initialization without jdtls_root (will try to detect)."""
        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        self.assertIsNone(client.jdtls_root)

    @patch("static_analyzer.lsp_client.java_client.find_java_21_or_later")
    @patch("static_analyzer.lsp_client.java_client.create_jdtls_command")
    @patch("static_analyzer.lsp_client.client.LSPClient.start")
    @patch("pathlib.Path.rglob")
    def test_start_success(self, mock_rglob, mock_super_start, mock_create_command, mock_find_java):
        """Test starting JavaClient successfully."""
        mock_find_java.return_value = Path("/usr/lib/jvm/java-21")
        mock_create_command.return_value = ["java", "-jar", "launcher.jar"]
        mock_rglob.return_value = []  # No java files for heap size calculation

        jdtls_root = Path("/opt/jdtls")
        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
            jdtls_root,
        )

        client.start()

        # Should have created workspace directory
        self.assertIsNotNone(client.workspace_dir)
        assert client.workspace_dir is not None  # Type narrowing for mypy
        self.assertTrue(client.workspace_dir.exists())

        # Should have found Java
        self.assertEqual(client.java_home, Path("/usr/lib/jvm/java-21"))

        # Should have created command
        mock_create_command.assert_called_once()

        # Should have called parent start
        mock_super_start.assert_called_once()

    @patch("static_analyzer.lsp_client.java_client.find_java_21_or_later")
    def test_start_no_java(self, mock_find_java):
        """Test error when Java 21+ not found."""
        mock_find_java.return_value = None

        jdtls_root = Path("/opt/jdtls")
        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
            jdtls_root,
        )

        with self.assertRaises(RuntimeError) as context:
            client.start()

        self.assertIn("Java 21+ required", str(context.exception))

    @patch("static_analyzer.lsp_client.java_client.find_java_21_or_later")
    @patch("pathlib.Path.exists")
    def test_start_no_jdtls_root(self, mock_exists, mock_find_java):
        """Test error when JDTLS not found."""
        mock_find_java.return_value = Path("/usr/lib/jvm/java-21")
        mock_exists.return_value = False

        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        with self.assertRaises(RuntimeError) as context:
            client.start()

        self.assertIn("JDTLS installation not found", str(context.exception))

    @patch("pathlib.Path.rglob")
    def test_calculate_heap_size_small_project(self, mock_rglob):
        """Test heap size calculation for small project."""
        # Mock < 100 files
        mock_rglob.return_value = [Path("file.java")] * 50

        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        heap_size = client._calculate_heap_size()
        self.assertEqual(heap_size, "1G")

    @patch("pathlib.Path.rglob")
    def test_calculate_heap_size_medium_project(self, mock_rglob):
        """Test heap size calculation for medium project."""
        # Mock 100-500 files
        mock_rglob.return_value = [Path("file.java")] * 300

        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        heap_size = client._calculate_heap_size()
        self.assertEqual(heap_size, "2G")

    @patch("pathlib.Path.rglob")
    def test_calculate_heap_size_large_project(self, mock_rglob):
        """Test heap size calculation for large project."""
        # Mock > 5000 files
        mock_rglob.return_value = [Path("file.java")] * 6000

        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        heap_size = client._calculate_heap_size()
        self.assertEqual(heap_size, "8G")

    @patch("static_analyzer.lsp_client.java_client.detect_java_installations")
    @patch("static_analyzer.lsp_client.java_client.get_java_version")
    def test_get_initialization_options(self, mock_version, mock_detect):
        """Test JDTLS initialization options."""
        mock_detect.return_value = [
            Path("/usr/lib/jvm/java-21"),
            Path("/usr/lib/jvm/java-17"),
        ]
        mock_version.side_effect = [21, 17]

        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )
        client.java_home = Path("/usr/lib/jvm/java-21")

        options = client._get_initialization_options()

        # Should have basic structure
        self.assertIn("bundles", options)
        self.assertIn("workspaceFolders", options)
        self.assertIn("settings", options)

        # Should have Java settings
        self.assertIn("java", options["settings"])
        java_settings = options["settings"]["java"]
        self.assertIn("home", java_settings)
        self.assertIn("configuration", java_settings)
        self.assertIn("import", java_settings)

        # Should have detected runtimes
        self.assertIn("runtimes", java_settings["configuration"])
        runtimes = java_settings["configuration"]["runtimes"]
        self.assertEqual(len(runtimes), 2)
        self.assertTrue(runtimes[0]["default"])  # First one is default

    @patch("static_analyzer.lsp_client.java_client.detect_java_installations")
    @patch("static_analyzer.lsp_client.java_client.get_java_version")
    def test_get_initialization_options_no_jdks(self, mock_version, mock_detect):
        """Test initialization options when no JDKs detected."""
        mock_detect.return_value = []

        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        options = client._get_initialization_options()

        # Should still work with empty runtimes
        runtimes = options["settings"]["java"]["configuration"]["runtimes"]
        self.assertEqual(len(runtimes), 0)

    def test_get_capabilities(self):
        """Test client capabilities."""
        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        capabilities = client._get_capabilities()

        # Should have text document capabilities
        self.assertIn("textDocument", capabilities)
        self.assertIn("callHierarchy", capabilities["textDocument"])
        self.assertIn("typeHierarchy", capabilities["textDocument"])

        # Should have workspace capabilities
        self.assertIn("workspace", capabilities)
        self.assertTrue(capabilities["workspace"]["workspaceFolders"])

    @patch("time.sleep")
    @patch("time.time")
    def test_wait_for_import_success(self, mock_time, mock_sleep):
        """Test waiting for import to complete."""
        mock_time.side_effect = [0, 1, 2, 3]  # Simulate time passing

        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )
        client._validate_project_loaded = Mock()  # type: ignore[method-assign]

        # Simulate import completing after 2 seconds
        def mark_complete(*args):
            client.import_complete = True

        mock_sleep.side_effect = [None, mark_complete(None)]

        client.wait_for_import(timeout=10)

        # Should have validated project
        client._validate_project_loaded.assert_called_once()

    @patch("time.sleep")
    @patch("time.time")
    def test_wait_for_import_timeout(self, mock_time, mock_sleep):
        """Test timeout when waiting for import."""
        # Simulate timeout
        mock_time.side_effect = [0] + [i for i in range(1, 400)]

        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )
        client._validate_project_loaded = Mock()  # type: ignore[method-assign]

        # Import never completes
        client.import_complete = False

        client.wait_for_import(timeout=1)

        # Should still validate even after timeout
        client._validate_project_loaded.assert_called_once()

    def test_validate_project_loaded_success(self):
        """Test project validation with workspace symbols."""
        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )
        client._send_request = Mock(return_value=1)  # type: ignore[method-assign]
        client._wait_for_response = Mock(return_value={"result": [{"name": "MyClass"}]})  # type: ignore[method-assign]

        # Should not raise
        client._validate_project_loaded()

        client._send_request.assert_called_with("workspace/symbol", {"query": ""})

    def test_validate_project_loaded_no_symbols(self):
        """Test project validation with no symbols."""
        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )
        client._send_request = Mock(return_value=1)  # type: ignore[method-assign]
        client._wait_for_response = Mock(return_value={"result": []})  # type: ignore[method-assign]

        # Should not raise, just log warning
        client._validate_project_loaded()

    def test_validate_project_loaded_error(self):
        """Test project validation with error."""
        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )
        client._send_request = Mock(return_value=1)  # type: ignore[method-assign]
        client._wait_for_response = Mock(return_value={"error": "Failed"})  # type: ignore[method-assign]

        # Should not raise, just log warning
        client._validate_project_loaded()

    def test_validate_project_loaded_exception(self):
        """Test project validation with exception."""
        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )
        client._send_request = Mock(side_effect=Exception("Test error"))  # type: ignore[method-assign]

        # Should not raise, just log warning
        client._validate_project_loaded()

    def test_handle_notification_import_started(self):
        """Test handling import started notification."""
        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        client.handle_notification("language/status", {"type": "Started"})

        # Should not crash
        self.assertFalse(client.import_complete)

    def test_handle_notification_import_complete(self):
        """Test handling import complete notification."""
        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        client.handle_notification("language/status", {"type": "ProjectStatus", "message": "OK"})

        # Should mark import as complete
        self.assertTrue(client.import_complete)

    def test_handle_notification_progress(self):
        """Test handling progress notifications."""
        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        client.handle_notification("$/progress", {"message": "Importing project..."})

        # Should not crash
        self.assertFalse(client.import_complete)

    def test_handle_notification_diagnostics(self):
        """Test handling diagnostic notifications."""
        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        diagnostics = [
            {"severity": 1, "message": "Project import failed"},
            {"severity": 2, "message": "Warning"},
        ]

        client.handle_notification("textDocument/publishDiagnostics", {"diagnostics": diagnostics})

        # Should record import error
        self.assertEqual(len(client.import_errors), 1)
        self.assertIn("import", client.import_errors[0].lower())

    @patch("shutil.rmtree")
    @patch("static_analyzer.lsp_client.client.LSPClient.close")
    def test_close_cleanup_workspace(self, mock_super_close, mock_rmtree):
        """Test cleanup of temporary workspace."""
        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )
        client.workspace_dir = Path("/tmp/jdtls-workspace-12345")
        client.temp_workspace = True

        # Mock workspace exists
        with patch.object(Path, "exists", return_value=True):
            client.close()

        # Should call parent close
        mock_super_close.assert_called_once()

        # Should remove workspace
        mock_rmtree.assert_called_once_with(client.workspace_dir)

    @patch("shutil.rmtree")
    @patch("static_analyzer.lsp_client.client.LSPClient.close")
    def test_close_no_cleanup_if_not_temp(self, mock_super_close, mock_rmtree):
        """Test no cleanup if workspace is not temporary."""
        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )
        client.workspace_dir = Path("/custom/workspace")
        client.temp_workspace = False

        client.close()

        # Should call parent close
        mock_super_close.assert_called_once()

        # Should NOT remove workspace
        mock_rmtree.assert_not_called()

    @patch("shutil.rmtree")
    @patch("static_analyzer.lsp_client.client.LSPClient.close")
    def test_close_cleanup_error_handling(self, mock_super_close, mock_rmtree):
        """Test error handling during workspace cleanup."""
        mock_rmtree.side_effect = OSError("Permission denied")

        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )
        client.workspace_dir = Path("/tmp/jdtls-workspace-12345")
        client.temp_workspace = True

        with patch.object(Path, "exists", return_value=True):
            # Should not raise, just log warning
            client.close()

        mock_super_close.assert_called_once()

    def test_get_package_name_from_declaration(self):
        """Test extracting package name from package declaration."""
        # Create Java file with package declaration
        java_file = self.project_path / "Test.java"
        java_file.write_text(
            """
            package com.example.myapp;

            public class Test {}
        """
        )

        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        package_name = client._get_package_name(java_file)
        self.assertEqual(package_name, "com.example.myapp")

    def test_get_package_name_from_path(self):
        """Test inferring package name from file path."""
        # Create Java file without package declaration
        src_dir = self.project_path / "src" / "main" / "java" / "com" / "example"
        src_dir.mkdir(parents=True)
        java_file = src_dir / "Test.java"
        java_file.write_text("public class Test {}")

        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        package_name = client._get_package_name(java_file)
        self.assertEqual(package_name, "com.example")

    def test_get_package_name_default_package(self):
        """Test default package for files in root."""
        java_file = self.project_path / "Test.java"
        java_file.write_text("public class Test {}")

        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        package_name = client._get_package_name(java_file)
        self.assertEqual(package_name, "default")

    def test_get_package_name_external(self):
        """Test package name for files outside project returns 'unknown' on error."""
        java_file = Path("/other/project/Test.java")

        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        # Files outside project will cause ValueError when trying relative_to
        # which is caught and returns "unknown"
        package_name = client._get_package_name(java_file)
        self.assertEqual(package_name, "unknown")

    def test_find_jdtls_root_from_locations(self):
        """Test finding JDTLS root from common locations."""
        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        # Use real temp directory for testing
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            jdtls_dir = Path(tmpdir) / ".jdtls"
            jdtls_dir.mkdir()
            (jdtls_dir / "plugins").mkdir()

            # Patch Path.home() to return our temp dir
            with patch("pathlib.Path.home", return_value=Path(tmpdir)):
                jdtls_root = client._find_jdtls_root()

                self.assertIsNotNone(jdtls_root)
                self.assertTrue(str(jdtls_root).endswith("/.jdtls"))

    def test_find_jdtls_root_not_found(self):
        """Test when JDTLS root is not found."""
        client = JavaClient(
            self.project_path,
            self.mock_language,
            self.project_config,
            self.mock_ignore_manager,
        )

        with patch.object(Path, "exists", return_value=False):
            jdtls_root = client._find_jdtls_root()

            self.assertIsNone(jdtls_root)


if __name__ == "__main__":
    unittest.main()
