import tempfile
import unittest
from pathlib import Path

from cache.static_cache import StaticAnalysisCache


class TestStaticAnalysisCache(unittest.TestCase):
    def test_default_cache_dir_uses_repo_local_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            static_cache = StaticAnalysisCache(repo_path=repo_dir, cache_dir=None)

            self.assertEqual(static_cache.cache_dir, repo_dir / ".codeboarding" / "cache")

    def test_get_client_cache_path_is_stable_and_scoped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_root = Path(temp_dir) / "cache"
            project_a = Path(temp_dir) / "project-a"
            project_b = Path(temp_dir) / "project-b"
            static_cache = StaticAnalysisCache(repo_path=project_a, cache_dir=cache_root)

            path_a_1 = static_cache.get_client_cache_path("python", project_a)
            path_a_2 = static_cache.get_client_cache_path("python", project_a)
            path_b = static_cache.get_client_cache_path("python", project_b)

            self.assertEqual(path_a_1, path_a_2)
            self.assertNotEqual(path_a_1, path_b)
            self.assertTrue(path_a_1.name.startswith("incremental_cache_python_"))


if __name__ == "__main__":
    unittest.main()
