"""Tests for the AnalysisCache class."""

import tempfile
import shutil
import unittest
from pathlib import Path

from static_analyzer.analysis_result import AnalysisCache, StaticAnalysisResults


class TestAnalysisCache(unittest.TestCase):
    """Tests for AnalysisCache save/load functionality."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cache_dir = Path(self.temp_dir) / "cache"
        self.cache = AnalysisCache(self.cache_dir)

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
        results = StaticAnalysisResults()
        results.add_source_files("python", ["src/main.py", "src/utils.py"])

        self.cache.save("my_hash", results)
        loaded = self.cache.get("my_hash")

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.get_source_files("python"), ["src/main.py", "src/utils.py"])

    def test_different_hashes_different_caches(self):
        """Different hashes should result in different cache files."""
        results1 = StaticAnalysisResults()
        results1.add_source_files("python", ["file1.py"])

        results2 = StaticAnalysisResults()
        results2.add_source_files("typescript", ["file2.ts"])

        self.cache.save("hash1", results1)
        self.cache.save("hash2", results2)

        loaded1 = self.cache.get("hash1")
        loaded2 = self.cache.get("hash2")

        self.assertEqual(loaded1.get_source_files("python"), ["file1.py"])
        self.assertEqual(loaded2.get_source_files("typescript"), ["file2.ts"])

    def test_get_returns_none_for_corrupted_cache(self):
        """get() should return None if cache file is corrupted."""
        self.cache_dir.mkdir(parents=True)
        cache_file = self.cache_dir / "corrupted_hash.pkl"
        cache_file.write_bytes(b"not a valid pickle")

        result = self.cache.get("corrupted_hash")
        self.assertIsNone(result)

    def test_save_overwrites_existing_cache(self):
        """save() should overwrite existing cache for the same hash."""
        results1 = StaticAnalysisResults()
        results1.add_source_files("python", ["old.py"])
        self.cache.save("same_hash", results1)

        results2 = StaticAnalysisResults()
        results2.add_source_files("python", ["new.py"])
        self.cache.save("same_hash", results2)

        loaded = self.cache.get("same_hash")
        self.assertEqual(loaded.get_source_files("python"), ["new.py"])

    def test_cache_file_naming(self):
        """Cache files should be named {hash}.pkl."""
        results = StaticAnalysisResults()
        self.cache.save("abc123_def456", results)

        expected_file = self.cache_dir / "abc123_def456.pkl"
        self.assertTrue(expected_file.exists())


class TestAnalysisCacheAtomicWrite(unittest.TestCase):
    """Tests for atomic write behavior of AnalysisCache."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cache_dir = Path(self.temp_dir) / "cache"
        self.cache = AnalysisCache(self.cache_dir)

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
