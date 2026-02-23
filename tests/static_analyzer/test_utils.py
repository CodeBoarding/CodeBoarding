import unittest
from pathlib import Path
from unittest.mock import patch

from utils import (
    CFGGenerationError,
    create_temp_repo_folder,
    get_config,
    remove_temp_repo_folder,
)


class TestUtils(unittest.TestCase):
    def test_cfg_generation_error(self):
        with self.assertRaises(CFGGenerationError):
            raise CFGGenerationError("Test error")

    def test_create_temp_repo_folder(self):
        temp_folder = create_temp_repo_folder()
        try:
            self.assertTrue(temp_folder.exists())
            self.assertTrue(temp_folder.is_dir())
            self.assertEqual(temp_folder.parts[0], "temp")
        finally:
            if temp_folder.exists():
                temp_folder.rmdir()

    def test_remove_temp_repo_folder_success(self):
        temp_folder = create_temp_repo_folder()
        self.assertTrue(temp_folder.exists())
        remove_temp_repo_folder(str(temp_folder))
        self.assertFalse(temp_folder.exists())

    def test_remove_temp_repo_folder_outside_temp_raises_error(self):
        with self.assertRaises(ValueError) as context:
            remove_temp_repo_folder("/some/other/path")
        self.assertIn("Refusing to delete outside of 'temp/'", str(context.exception))

    def test_remove_temp_repo_folder_relative_path_outside_temp(self):
        with self.assertRaises(ValueError):
            remove_temp_repo_folder("not_temp/folder")

    def test_get_config_returns_lsp_servers(self):
        fake_config = {
            "lsp_servers": {"python": {"command": ["/fake/pyright", "--stdio"]}},
            "tools": {"tokei": {"command": ["/fake/tokei", "-o", "json"]}},
        }
        with patch("tool_registry.build_config", return_value=fake_config):
            result = get_config("lsp_servers")
            self.assertIn("python", result)

    def test_get_config_missing_key_raises(self):
        fake_config: dict[str, dict[str, dict]] = {"lsp_servers": {}, "tools": {}}
        with patch("tool_registry.build_config", return_value=fake_config):
            with self.assertRaises(KeyError) as ctx:
                get_config("nonexistent_key")
            self.assertIn("not found in configuration", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
