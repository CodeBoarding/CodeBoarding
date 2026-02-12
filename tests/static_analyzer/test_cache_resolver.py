import tempfile
import unittest
from pathlib import Path

from static_analyzer.cache_resolver import resolve_incremental_cache_path, resolve_static_cache_dir


class TestStaticCacheResolver(unittest.TestCase):
    def test_resolve_static_cache_dir_uses_repo_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            cache_dir = resolve_static_cache_dir(repo_dir, None)
            self.assertEqual(cache_dir, repo_dir / ".codeboarding" / "cache")

    def test_resolve_incremental_cache_path_is_stable_and_scoped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_root = Path(temp_dir) / "cache"
            project_a = Path(temp_dir) / "project-a"
            project_b = Path(temp_dir) / "project-b"

            path_a_1 = resolve_incremental_cache_path(cache_root, "python", project_a)
            path_a_2 = resolve_incremental_cache_path(cache_root, "python", project_a)
            path_b = resolve_incremental_cache_path(cache_root, "python", project_b)

            self.assertEqual(path_a_1, path_a_2)
            self.assertNotEqual(path_a_1, path_b)
            self.assertTrue(path_a_1.name.startswith("incremental_cache_python_"))


if __name__ == "__main__":
    unittest.main()
