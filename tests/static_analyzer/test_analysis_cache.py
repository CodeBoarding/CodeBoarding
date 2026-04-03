"""Tests for the StaticAnalysisCache class."""

import tempfile
import shutil
import unittest
from pathlib import Path

from static_analyzer.analysis_result import StaticAnalysisCache, StaticAnalysisResults


class TestStaticAnalysisCache(unittest.TestCase):
    """Tests for StaticAnalysisCache save/load functionality."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cache_dir = Path(self.temp_dir) / "cache"
        self.repo_root = Path(self.temp_dir)
        self.cache = StaticAnalysisCache(self.cache_dir, self.repo_root)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_returns_none_for_missing_cache(self):
        """get() should return None when cache file doesn't exist."""
        result = self.cache.get("nonexistent_hash")
        self.assertIsNone(result)

    def test_save_creates_cache_directory(self):
        """save() should create the cache directory if it doesn't exist."""
        self.assertFalse(self.cache_dir.exists())

        results = StaticAnalysisResults()
        self.cache.save("test_hash", results)

        self.assertTrue(self.cache_dir.exists())

    def test_save_and_get_roundtrip(self):
        """Saved results should be retrievable with get()."""
        file1 = str(self.repo_root / "src/main.py")
        file2 = str(self.repo_root / "src/utils.py")
        results = StaticAnalysisResults()
        results.add_source_files("python", [file1, file2])

        self.cache.save("my_hash", results)
        loaded = self.cache.get("my_hash")

        self.assertIsNotNone(loaded)
        if loaded is None:
            return

        self.assertEqual(loaded.get_source_files("python"), [file1, file2])

    def test_different_hashes_different_caches(self):
        """Different hashes should result in different cache files."""
        file1 = str(self.repo_root / "file1.py")
        file2 = str(self.repo_root / "file2.ts")

        results1 = StaticAnalysisResults()
        results1.add_source_files("python", [file1])

        results2 = StaticAnalysisResults()
        results2.add_source_files("typescript", [file2])

        self.cache.save("hash1", results1)
        self.cache.save("hash2", results2)

        loaded1 = self.cache.get("hash1")
        loaded2 = self.cache.get("hash2")

        self.assertIsNotNone(loaded1)
        self.assertIsNotNone(loaded2)
        if loaded1 is None or loaded2 is None:
            return

        self.assertEqual(loaded1.get_source_files("python"), [file1])
        self.assertEqual(loaded2.get_source_files("typescript"), [file2])

    def test_get_returns_none_for_corrupted_cache(self):
        """get() should return None if cache file is corrupted."""
        self.cache_dir.mkdir(parents=True)
        cache_file = self.cache_dir / "corrupted_hash.pkl"
        cache_file.write_bytes(b"not a valid pickle")

        result = self.cache.get("corrupted_hash")
        self.assertIsNone(result)

    def test_save_overwrites_existing_cache(self):
        """save() should overwrite existing cache for the same hash."""
        old_file = str(self.repo_root / "old.py")
        new_file = str(self.repo_root / "new.py")

        results1 = StaticAnalysisResults()
        results1.add_source_files("python", [old_file])
        self.cache.save("same_hash", results1)

        results2 = StaticAnalysisResults()
        results2.add_source_files("python", [new_file])
        self.cache.save("same_hash", results2)

        loaded = self.cache.get("same_hash")
        self.assertIsNotNone(loaded)
        if loaded is None:
            return

        self.assertEqual(loaded.get_source_files("python"), [new_file])

    def test_cache_file_naming(self):
        """Cache files should be named {hash}.pkl."""
        results = StaticAnalysisResults()
        self.cache.save("abc123_def456", results)

        expected_file = self.cache_dir / "abc123_def456.pkl"
        self.assertTrue(expected_file.exists())


class TestStaticAnalysisCacheAtomicWrite(unittest.TestCase):
    """Tests for atomic write behavior of StaticAnalysisCache."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cache_dir = Path(self.temp_dir) / "cache"
        self.repo_root = Path(self.temp_dir)
        self.cache = StaticAnalysisCache(self.cache_dir, self.repo_root)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_no_temp_files_after_save(self):
        """No .tmp files should remain after successful save."""
        results = StaticAnalysisResults()
        self.cache.save("test_hash", results)

        tmp_files = list(self.cache_dir.glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0)


if __name__ == "__main__":
    unittest.main()
