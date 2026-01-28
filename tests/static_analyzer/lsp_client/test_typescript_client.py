import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from static_analyzer.lsp_client.typescript_client import TypeScriptClient
from static_analyzer.scanner import ProgrammingLanguage


class TestTypeScriptClient(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for testing
        self.temp_dir = tempfile.mkdtemp()
        self.project_path = Path(self.temp_dir)

        # Create mock language
        self.mock_language = Mock(spec=ProgrammingLanguage)
        self.mock_language.get_server_parameters.return_value = ["typescript-language-server", "--stdio"]
        self.mock_language.get_suffix_pattern.return_value = ["*.ts", "*.tsx", "*.js", "*.jsx"]
        self.mock_language.get_language_id.return_value = "typescript"

    def tearDown(self):
        # Clean up temporary directory
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("static_analyzer.lsp_client.client.LSPClient.start")
    @patch("subprocess.Popen")
    def test_start_with_node_modules(self, mock_popen, mock_super_start):
        # Test starting when node_modules exists
        mock_process = Mock()
        mock_popen.return_value = mock_process

        # Create node_modules directory
        node_modules = self.project_path / "node_modules"
        node_modules.mkdir()

        client = TypeScriptClient(self.project_path, self.mock_language)
        client.start()

        # Should call parent start
        mock_super_start.assert_called_once()

    @patch("static_analyzer.lsp_client.client.LSPClient.start")
    @patch("subprocess.Popen")
    def test_start_without_node_modules(self, mock_popen, mock_super_start):
        # Test starting when node_modules doesn't exist
        mock_process = Mock()
        mock_popen.return_value = mock_process

        # Create package.json
        package_json = self.project_path / "package.json"
        package_json.write_text('{"name": "test"}')

        client = TypeScriptClient(self.project_path, self.mock_language)
        client.start()

        # Should still call parent start
        mock_super_start.assert_called_once()

    @patch("static_analyzer.lsp_client.client.LSPClient.start")
    @patch("subprocess.Popen")
    def test_start_no_package_json(self, mock_popen, mock_super_start):
        # Test starting when neither node_modules nor package.json exist
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)
        client.start()

        # Should still call parent start
        mock_super_start.assert_called_once()

    @patch("subprocess.Popen")
    def test_ensure_dependencies_with_node_modules(self, mock_popen):
        # Test dependency check when node_modules exists
        mock_process = Mock()
        mock_popen.return_value = mock_process

        # Create node_modules
        node_modules = self.project_path / "node_modules"
        node_modules.mkdir()

        client = TypeScriptClient(self.project_path, self.mock_language)
        client._ensure_dependencies()

        # Should not raise any errors
        self.assertTrue(node_modules.exists())

    @patch("subprocess.Popen")
    def test_ensure_dependencies_without_node_modules_no_package_json(self, mock_popen):
        # Test dependency check without node_modules and package.json
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)
        client._ensure_dependencies()

        # Should just log warnings, not raise errors

    @patch("subprocess.Popen")
    def test_customize_initialization_params(self, mock_popen):
        # Test customization of initialization parameters
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)

        base_params = {
            "processId": 12345,
            "rootUri": self.project_path.as_uri(),
            "capabilities": {},
        }

        customized = client._customize_initialization_params(base_params)

        # Should add TypeScript-specific params
        self.assertIn("workspaceFolders", customized)
        self.assertIn("initializationOptions", customized)
        self.assertEqual(len(customized["workspaceFolders"]), 1)
        self.assertEqual(customized["workspaceFolders"][0]["name"], self.project_path.name)

    @patch("subprocess.Popen")
    def test_customize_initialization_params_includes_preferences(self, mock_popen):
        # Test that initialization params include TypeScript preferences
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)

        base_params: dict = {"capabilities": {}}
        customized = client._customize_initialization_params(base_params)

        # Should include preferences
        self.assertIn("preferences", customized["initializationOptions"])
        self.assertIn("includeCompletionsForModuleExports", customized["initializationOptions"]["preferences"])

    @patch("subprocess.Popen")
    def test_find_typescript_files(self, mock_popen):
        # Test finding TypeScript files
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)

        # Create test files
        (self.project_path / "src").mkdir()
        (self.project_path / "file1.ts").touch()
        (self.project_path / "file2.tsx").touch()
        (self.project_path / "file3.js").touch()
        (self.project_path / "src" / "file4.jsx").touch()
        (self.project_path / "other.txt").touch()

        ts_files = client._find_typescript_files()

        # Should find all TS/JS files
        self.assertEqual(len(ts_files), 4)
        extensions = [f.suffix for f in ts_files]
        self.assertIn(".ts", extensions)
        self.assertIn(".tsx", extensions)
        self.assertIn(".js", extensions)
        self.assertIn(".jsx", extensions)

    @patch("subprocess.Popen")
    def test_get_source_files_excludes_node_modules(self, mock_popen):
        # Test that node_modules is excluded from source files
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)

        # Create test files
        (self.project_path / "src").mkdir()
        (self.project_path / "node_modules").mkdir()
        (self.project_path / "src" / "app.ts").touch()
        (self.project_path / "node_modules" / "lib.ts").touch()

        src_files = client._get_source_files()

        # Should exclude node_modules
        file_names = [f.name for f in src_files]
        self.assertIn("app.ts", file_names)
        self.assertNotIn("lib.ts", file_names)

    @patch("subprocess.Popen")
    def test_get_source_files_excludes_dist(self, mock_popen):
        # Test that dist directory is excluded
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)

        # Create test files
        (self.project_path / "src").mkdir()
        (self.project_path / "dist").mkdir()
        (self.project_path / "src" / "app.ts").touch()
        (self.project_path / "dist" / "app.js").touch()

        src_files = client._get_source_files()

        # Should exclude dist
        paths_str = [str(f) for f in src_files]
        self.assertTrue(any("src" in p for p in paths_str))
        self.assertFalse(any("dist" in p for p in paths_str))

    @patch("subprocess.Popen")
    def test_process_config_files_tsconfig(self, mock_popen):
        # Test processing tsconfig.json
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)
        client._send_notification = Mock()  # type: ignore[method-assign]

        # Create tsconfig.json
        tsconfig = self.project_path / "tsconfig.json"
        tsconfig.write_text('{"compilerOptions": {}}')

        result = client._process_config_files()

        # Should return True and send notification
        self.assertTrue(result)
        client._send_notification.assert_called()

    @patch("subprocess.Popen")
    def test_process_config_files_jsconfig(self, mock_popen):
        # Test processing jsconfig.json
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)
        client._send_notification = Mock()  # type: ignore[method-assign]

        # Create jsconfig.json
        jsconfig = self.project_path / "jsconfig.json"
        jsconfig.write_text('{"compilerOptions": {}}')

        result = client._process_config_files()

        # Should return True
        self.assertTrue(result)

    @patch("subprocess.Popen")
    def test_process_config_files_package_json(self, mock_popen):
        # Test processing package.json
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)
        client._send_notification = Mock()  # type: ignore[method-assign]

        # Create package.json
        package_json = self.project_path / "package.json"
        package_json.write_text('{"name": "test"}')

        result = client._process_config_files()

        # Should return True
        self.assertTrue(result)

    @patch("subprocess.Popen")
    def test_process_config_files_none_found(self, mock_popen):
        # Test when no config files exist
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)
        client._send_notification = Mock()  # type: ignore[method-assign]

        result = client._process_config_files()

        # Should return False
        self.assertFalse(result)

    @patch("subprocess.Popen")
    @patch("time.sleep")
    def test_bootstrap_project(self, mock_sleep, mock_popen):
        # Test bootstrapping TypeScript project
        import pathspec

        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)
        client._send_notification = Mock()  # type: ignore[method-assign]
        client._validate_typescript_project = Mock(return_value=True)  # type: ignore[method-assign]
        client.get_exclude_dirs = Mock(return_value=pathspec.PathSpec.from_lines("gitwildmatch", []))  # type: ignore[method-assign]

        # Create test files
        (self.project_path / "src").mkdir()
        (self.project_path / "src" / "file1.ts").touch()
        (self.project_path / "src" / "file2.ts").touch()
        (self.project_path / "src" / "file3.ts").touch()

        ts_files = [
            self.project_path / "src" / "file1.ts",
            self.project_path / "src" / "file2.ts",
            self.project_path / "src" / "file3.ts",
        ]

        client._bootstrap_project(ts_files, config_found=True)

        # Should open sample files
        self.assertGreaterEqual(client._send_notification.call_count, 3)
        # Should validate project
        client._validate_typescript_project.assert_called()

    @patch("subprocess.Popen")
    @patch("time.sleep")
    def test_bootstrap_project_no_config(self, mock_sleep, mock_popen):
        # Test bootstrapping without config files (longer wait time)
        import pathspec

        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)
        client._send_notification = Mock()  # type: ignore[method-assign]
        client._validate_typescript_project = Mock(return_value=True)  # type: ignore[method-assign]
        client.get_exclude_dirs = Mock(return_value=pathspec.PathSpec.from_lines("gitwildmatch", []))  # type: ignore[method-assign]

        (self.project_path / "file.ts").touch()
        ts_files = [self.project_path / "file.ts"]

        client._bootstrap_project(ts_files, config_found=False)

        # Should wait longer when no config found
        # Check that sleep was called with 8 seconds
        sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]
        self.assertIn(8, sleep_calls)

    @patch("subprocess.Popen")
    def test_close_bootstrap_files(self, mock_popen):
        # Test closing bootstrap files
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)
        client._send_notification = Mock()  # type: ignore[method-assign]

        # Create sample files
        file1 = self.project_path / "file1.ts"
        file2 = self.project_path / "file2.ts"
        file1.touch()
        file2.touch()

        sample_files = [file1, file2]

        client._close_bootstrap_files(sample_files)

        # Should send close notification for each file
        self.assertEqual(client._send_notification.call_count, 2)

    @patch("subprocess.Popen")
    @patch("time.sleep")
    def test_prepare_for_analysis(self, mock_sleep, mock_popen):
        # Test preparation before analysis
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)
        client._send_request = Mock(return_value=1)  # type: ignore[method-assign]
        client._wait_for_response = Mock(return_value={"result": [{"name": "TestClass"}]})  # type: ignore[method-assign]

        client._prepare_for_analysis()

        # Should sleep for 2 seconds before validation
        mock_sleep.assert_called_with(2)
        # Should have called workspace/symbol
        client._send_request.assert_called_with("workspace/symbol", {"query": ""})

    @patch("subprocess.Popen")
    def test_configure_typescript_workspace_no_files(self, mock_popen):
        # Test workspace configuration when no TypeScript files found
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)
        client._find_typescript_files = Mock(return_value=[])  # type: ignore[method-assign]
        client._send_notification = Mock()  # type: ignore[method-assign]

        client._configure_typescript_workspace()

        # Should return early, minimal notifications
        self.assertLessEqual(client._send_notification.call_count, 1)

    @patch("subprocess.Popen")
    @patch("time.sleep")
    def test_configure_typescript_workspace_with_files(self, mock_sleep, mock_popen):
        # Test workspace configuration with TypeScript files
        import pathspec

        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)

        # Create test files
        (self.project_path / "app.ts").touch()

        client._send_notification = Mock()  # type: ignore[method-assign]
        client._validate_typescript_project = Mock(return_value=True)  # type: ignore[method-assign]
        client.get_exclude_dirs = Mock(return_value=pathspec.PathSpec.from_lines("gitwildmatch", []))  # type: ignore[method-assign]

        client._configure_typescript_workspace()

        # Should send notifications
        self.assertGreater(client._send_notification.call_count, 0)

    @patch("subprocess.Popen")
    def test_configure_typescript_workspace_exception_handling(self, mock_popen):
        # Test that exceptions in workspace configuration are handled gracefully
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)
        client._find_typescript_files = Mock(side_effect=Exception("Test error"))  # type: ignore[method-assign]

        # Should not raise, just log warning
        try:
            client._configure_typescript_workspace()
        except Exception:
            self.fail("configure_typescript_workspace should not raise exceptions")

    @patch("subprocess.Popen")
    def test_handle_notification(self, mock_popen):
        """Test that TypeScriptClient has handle_notification method that does nothing."""
        mock_process = Mock()
        mock_popen.return_value = mock_process

        client = TypeScriptClient(self.project_path, self.mock_language)

        # Should not crash and should do nothing
        client.handle_notification("window/logMessage", {"message": "test"})
        client.handle_notification("textDocument/publishDiagnostics", {"diagnostics": []})

        # Verify it exists and is callable
        self.assertTrue(callable(client.handle_notification))


if __name__ == "__main__":
    unittest.main()
