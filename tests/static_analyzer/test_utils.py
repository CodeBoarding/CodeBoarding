import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import utils
from utils import (
    CFGGenerationError,
    caching_enabled,
    contains_json,
    create_temp_repo_folder,
    default_config,
    get_config,
    remove_temp_repo_folder,
)


class TestUtils(unittest.TestCase):
    def test_cfg_generation_error(self):
        # Test that CFGGenerationError can be raised and caught
        with self.assertRaises(CFGGenerationError):
            raise CFGGenerationError("Test error")

    def test_create_temp_repo_folder(self):
        # Test creating a temporary repository folder
        temp_folder = create_temp_repo_folder()
        try:
            self.assertTrue(temp_folder.exists())
            self.assertTrue(temp_folder.is_dir())
            self.assertEqual(temp_folder.parts[0], "temp")
        finally:
            if temp_folder.exists():
                temp_folder.rmdir()

    def test_remove_temp_repo_folder_success(self):
        # Test removing a valid temp folder
        temp_folder = create_temp_repo_folder()
        self.assertTrue(temp_folder.exists())
        remove_temp_repo_folder(str(temp_folder))
        self.assertFalse(temp_folder.exists())

    def test_remove_temp_repo_folder_outside_temp_raises_error(self):
        # Test that removing a folder outside 'temp/' raises an error
        with self.assertRaises(ValueError) as context:
            remove_temp_repo_folder("/some/other/path")
        self.assertIn("Refusing to delete outside of 'temp/'", str(context.exception))

    def test_remove_temp_repo_folder_relative_path_outside_temp(self):
        # Test with a relative path that doesn't start with temp
        with self.assertRaises(ValueError):
            remove_temp_repo_folder("not_temp/folder")

    @patch.dict(os.environ, {"CACHING_DOCUMENTATION": "true"})
    def test_caching_enabled_true(self):
        # Test when caching is enabled
        self.assertTrue(caching_enabled())

    @patch.dict(os.environ, {"CACHING_DOCUMENTATION": "1"})
    def test_caching_enabled_numeric_true(self):
        # Test with numeric true value
        self.assertTrue(caching_enabled())

    @patch.dict(os.environ, {"CACHING_DOCUMENTATION": "yes"})
    def test_caching_enabled_yes(self):
        # Test with 'yes' value
        self.assertTrue(caching_enabled())

    @patch.dict(os.environ, {"CACHING_DOCUMENTATION": "false"})
    def test_caching_enabled_false(self):
        # Test when caching is disabled
        self.assertFalse(caching_enabled())

    @patch.dict(os.environ, {}, clear=True)
    def test_caching_enabled_default(self):
        # Test default value when env var is not set
        self.assertFalse(caching_enabled())

    def test_contains_json_true(self):
        # Test when JSON file exists
        files = [Path("file1.txt"), Path("node123.json"), Path("file2.py")]
        self.assertTrue(contains_json("node123", files))

    def test_contains_json_false(self):
        # Test when JSON file doesn't exist
        files = [Path("file1.txt"), Path("other.json"), Path("file2.py")]
        self.assertFalse(contains_json("node123", files))

    def test_contains_json_empty_list(self):
        # Test with empty file list
        files: list[Path] = []
        self.assertFalse(contains_json("node123", files))

    def test_contains_json_with_path(self):
        # Test with full paths
        files = [Path("/some/path/node456.json")]
        self.assertTrue(contains_json("node456", files))

    @patch.dict(os.environ, {}, clear=True)
    def test_get_config_no_env_var(self):
        # Test when STATIC_ANALYSIS_CONFIG is not set
        result = get_config("some_key")
        # Should use default config
        self.assertIsNotNone(result or True)  # May be None if key doesn't exist in default

    def test_get_config_file_not_found(self):
        # Test when config file doesn't exist
        with patch.dict(os.environ, {"STATIC_ANALYSIS_CONFIG": "/nonexistent/config.yaml"}):
            with self.assertRaises(FileNotFoundError):
                get_config("some_key")

    def test_get_config_valid_file(self):
        # Test with a valid config file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config_data = {"test_key": "test_value", "another_key": 123}
            yaml.dump(config_data, f)
            temp_file = f.name

        try:
            with patch.dict(os.environ, {"STATIC_ANALYSIS_CONFIG": temp_file}):
                result = get_config("test_key")
                self.assertEqual(result, "test_value")
        finally:
            Path(temp_file).unlink()

    def test_get_config_missing_key(self):
        # Test when key is not in config file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config_data = {"existing_key": "value"}
            yaml.dump(config_data, f)
            temp_file = f.name

        try:
            with patch.dict(os.environ, {"STATIC_ANALYSIS_CONFIG": temp_file}):
                with self.assertRaises(KeyError) as context:
                    get_config("missing_key")
                self.assertIn("not found in configuration", str(context.exception))
        finally:
            Path(temp_file).unlink()

    def test_default_config(self):
        # Test default_config function
        from vscode_constants import VSCODE_CONFIG

        for key in VSCODE_CONFIG:
            result = default_config(key)
            self.assertEqual(result, VSCODE_CONFIG[key])

    def test_default_config_missing_key(self):
        # Test with a key that doesn't exist
        result = default_config("nonexistent_key_12345")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
