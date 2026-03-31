import unittest
from pathlib import Path
from unittest.mock import patch

from utils import (
    CFGGenerationError,
    create_temp_repo_folder,
    get_config,
    remove_temp_repo_folder,
    to_absolute_path,
    to_relative_path,
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


class TestToRelativePath(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path("/repo/root")

    def test_absolute_path_under_root_becomes_relative(self):
        result = to_relative_path("/repo/root/src/main.py", self.repo_root)
        self.assertEqual(result, "src/main.py")

    def test_uses_forward_slashes(self):
        # Result must always use forward slashes for cross-platform portability.
        result = to_relative_path("/repo/root/src/pkg/module.py", self.repo_root)
        self.assertNotIn("\\", result)
        self.assertEqual(result, "src/pkg/module.py")

    def test_nested_path_preserved(self):
        result = to_relative_path("/repo/root/a/b/c/file.py", self.repo_root)
        self.assertEqual(result, "a/b/c/file.py")

    def test_path_outside_root_returned_unchanged(self):
        outside = "/other/machine/path/file.py"
        result = to_relative_path(outside, self.repo_root)
        self.assertEqual(result, outside)

    def test_roundtrip_relative_then_absolute(self):
        original = "/repo/root/src/app.py"
        relative = to_relative_path(original, self.repo_root)
        restored = to_absolute_path(relative, self.repo_root)
        self.assertEqual(restored, original)


class TestToAbsolutePath(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path("/repo/root")

    def test_relative_path_resolved_against_root(self):
        result = to_absolute_path("src/main.py", self.repo_root)
        self.assertEqual(result, "/repo/root/src/main.py")

    def test_already_absolute_path_returned_unchanged(self):
        abs_path = "/repo/root/src/main.py"
        result = to_absolute_path(abs_path, self.repo_root)
        self.assertEqual(result, abs_path)

    def test_windows_backslash_separators_normalised(self):
        # A path written on Windows must resolve correctly on POSIX.
        windows_relative = "src\\pkg\\module.py"
        result = to_absolute_path(windows_relative, self.repo_root)
        self.assertEqual(result, "/repo/root/src/pkg/module.py")

    def test_windows_backslash_nested(self):
        windows_relative = "a\\b\\c\\file.py"
        result = to_absolute_path(windows_relative, self.repo_root)
        self.assertEqual(result, "/repo/root/a/b/c/file.py")

    def test_roundtrip_absolute_then_relative(self):
        original = "src/utils.py"
        absolute = to_absolute_path(original, self.repo_root)
        restored = to_relative_path(absolute, self.repo_root)
        self.assertEqual(restored, original)


if __name__ == "__main__":
    unittest.main()
