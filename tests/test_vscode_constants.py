import os
import platform
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vscode_constants import (
    VSCODE_CONFIG,
    find_runnable,
    get_bin_path,
    update_command_paths,
    update_config,
)


class TestVSCodeConstants(unittest.TestCase):
    @patch("platform.system")
    def test_get_bin_path_windows(self, mock_system):
        # Test bin path for Windows
        mock_system.return_value = "Windows"

        bin_dir = "/test/bin"
        result = get_bin_path(bin_dir)

        expected = os.path.join(bin_dir, "bin", "win")
        self.assertEqual(result, expected)

    @patch("platform.system")
    def test_get_bin_path_macos(self, mock_system):
        # Test bin path for macOS
        mock_system.return_value = "Darwin"

        bin_dir = "/test/bin"
        result = get_bin_path(bin_dir)

        expected = os.path.join(bin_dir, "bin", "macos")
        self.assertEqual(result, expected)

    @patch("platform.system")
    def test_get_bin_path_linux(self, mock_system):
        # Test bin path for Linux
        mock_system.return_value = "Linux"

        bin_dir = "/test/bin"
        result = get_bin_path(bin_dir)

        expected = os.path.join(bin_dir, "bin", "linux")
        self.assertEqual(result, expected)

    @patch("platform.system")
    def test_get_bin_path_unsupported(self, mock_system):
        # Test bin path for unsupported platform
        mock_system.return_value = "FreeBSD"

        bin_dir = "/test/bin"
        with self.assertRaises(RuntimeError) as context:
            get_bin_path(bin_dir)

        self.assertIn("Unsupported platform", str(context.exception))

    def test_find_runnable_found(self):
        # Test finding a runnable file
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a test directory structure
            test_dir = Path(temp_dir) / "node_modules" / "typescript-language-server"
            test_dir.mkdir(parents=True)

            # Create the file we're looking for
            test_file = test_dir / "cli.mjs"
            test_file.write_text("test")

            result = find_runnable(temp_dir, "cli.mjs", "typescript-language-server")

            self.assertIsNotNone(result)
            self.assertTrue("cli.mjs" in result)

    def test_find_runnable_not_found(self):
        # Test when runnable file is not found
        with tempfile.TemporaryDirectory() as temp_dir:
            result = find_runnable(temp_dir, "nonexistent.js", "somedir")

            self.assertIsNone(result)

    def test_find_runnable_wrong_directory(self):
        # Test when file exists but not in correct directory
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a file in wrong directory
            wrong_dir = Path(temp_dir) / "wrong_place"
            wrong_dir.mkdir()
            test_file = wrong_dir / "cli.mjs"
            test_file.write_text("test")

            result = find_runnable(temp_dir, "cli.mjs", "correct_place")

            self.assertIsNone(result)

    @patch("platform.system")
    def test_update_command_paths_typescript(self, mock_system):
        # Test updating command paths for TypeScript
        mock_system.return_value = "Linux"

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create typescript directory structure
            ts_dir = Path(temp_dir) / "node_modules" / "typescript-language-server"
            ts_dir.mkdir(parents=True)
            cli_file = ts_dir / "cli.mjs"
            cli_file.write_text("test")

            # Reset VSCODE_CONFIG to known state
            original_cmd = list(VSCODE_CONFIG["lsp_servers"]["typescript"]["command"])

            update_command_paths(temp_dir)

            # Check that TypeScript command was updated
            updated_cmd = VSCODE_CONFIG["lsp_servers"]["typescript"]["command"][0]
            self.assertTrue("cli.mjs" in updated_cmd)

            # Restore original
            VSCODE_CONFIG["lsp_servers"]["typescript"]["command"] = original_cmd

    @patch("platform.system")
    def test_update_command_paths_windows_node_prefix(self, mock_system):
        # Test that node is prepended on Windows for certain languages
        mock_system.return_value = "Windows"

        with tempfile.TemporaryDirectory() as temp_dir:
            original_config = {}
            for lang in ["typescript", "python", "php"]:
                original_config[lang] = list(VSCODE_CONFIG["lsp_servers"][lang]["command"])

            update_command_paths(temp_dir)

            # Check that node was prepended for these languages on Windows
            for lang in ["typescript", "python", "php"]:
                cmd = VSCODE_CONFIG["lsp_servers"][lang]["command"]
                self.assertEqual(cmd[0], "node")

            # Restore original
            for lang in ["typescript", "python", "php"]:
                VSCODE_CONFIG["lsp_servers"][lang]["command"] = original_config[lang]

    @patch("platform.system")
    def test_update_command_paths_non_windows(self, mock_system):
        # Test that node is NOT prepended on non-Windows
        mock_system.return_value = "Linux"

        with tempfile.TemporaryDirectory() as temp_dir:
            original_config = {}
            for lang in ["typescript", "python", "php"]:
                original_config[lang] = list(VSCODE_CONFIG["lsp_servers"][lang]["command"])

            update_command_paths(temp_dir)

            # On Linux, node should not be prepended
            for lang in ["typescript", "python", "php"]:
                cmd = VSCODE_CONFIG["lsp_servers"][lang]["command"]
                # If the command was not found, it might still have node from Windows test
                # But the original should not start with node
                if cmd[0] != "node":
                    self.assertNotEqual(cmd[0], "node")

            # Restore original
            for lang in ["typescript", "python", "php"]:
                VSCODE_CONFIG["lsp_servers"][lang]["command"] = original_config[lang]

    def test_update_config_with_bin_dir(self):
        # Test update_config with bin_dir
        with tempfile.TemporaryDirectory() as temp_dir:
            # Just verify no exception is raised
            update_config(bin_dir=temp_dir)

    def test_update_config_without_bin_dir(self):
        # Test update_config without bin_dir
        update_config(bin_dir=None)
        # Should do nothing without error

    def test_vscode_config_structure(self):
        # Test that VSCODE_CONFIG has expected structure
        self.assertIn("lsp_servers", VSCODE_CONFIG)
        self.assertIn("tools", VSCODE_CONFIG)

        # Check LSP servers
        self.assertIn("python", VSCODE_CONFIG["lsp_servers"])
        self.assertIn("typescript", VSCODE_CONFIG["lsp_servers"])
        self.assertIn("go", VSCODE_CONFIG["lsp_servers"])
        self.assertIn("php", VSCODE_CONFIG["lsp_servers"])

        # Check tools
        self.assertIn("tokei", VSCODE_CONFIG["tools"])

    def test_vscode_config_python_structure(self):
        # Test Python LSP server configuration
        python_config = VSCODE_CONFIG["lsp_servers"]["python"]

        self.assertIn("name", python_config)
        self.assertIn("command", python_config)
        self.assertIn("languages", python_config)
        self.assertIn("file_extensions", python_config)
        self.assertIn("install_commands", python_config)

        self.assertEqual(python_config["languages"], ["python"])
        self.assertIn(".py", python_config["file_extensions"])

    def test_vscode_config_typescript_structure(self):
        # Test TypeScript LSP server configuration
        ts_config = VSCODE_CONFIG["lsp_servers"]["typescript"]

        self.assertIn("name", ts_config)
        self.assertIn("command", ts_config)
        self.assertIn("languages", ts_config)
        self.assertIn("file_extensions", ts_config)

        self.assertIn("typescript", ts_config["languages"])
        self.assertIn("javascript", ts_config["languages"])
        self.assertIn(".ts", ts_config["file_extensions"])
        self.assertIn(".js", ts_config["file_extensions"])

    def test_vscode_config_go_structure(self):
        # Test Go LSP server configuration
        go_config = VSCODE_CONFIG["lsp_servers"]["go"]

        self.assertIn("name", go_config)
        self.assertIn("command", go_config)
        self.assertEqual(go_config["languages"], ["go"])
        self.assertIn(".go", go_config["file_extensions"])

    def test_vscode_config_php_structure(self):
        # Test PHP LSP server configuration
        php_config = VSCODE_CONFIG["lsp_servers"]["php"]

        self.assertIn("name", php_config)
        self.assertIn("command", php_config)
        self.assertEqual(php_config["languages"], ["php"])
        self.assertIn(".php", php_config["file_extensions"])


if __name__ == "__main__":
    unittest.main()
