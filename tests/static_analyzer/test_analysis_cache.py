"""Tests for the StaticAnalysisCache class."""

import shutil
import tempfile
import unittest
from pathlib import Path

from static_analyzer.analysis_cache import (
    STATIC_ANALYSIS_PKL,
    STATIC_ANALYSIS_SHA,
    StaticAnalysisCache,
    copy_cache_files,
)
from static_analyzer.analysis_result import StaticAnalysisResults


class TestStaticAnalysisCache(unittest.TestCase):
    """Tests for StaticAnalysisCache save/load functionality."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        # Artifact dir is the sibling-of-analysis.json directory; tests use a
        # subdir under temp so the legacy ``cache/`` migration fallback can
        # also be exercised at ``<artifact_dir>/cache/``.
        self.artifact_dir = Path(self.temp_dir) / ".codeboarding"
        self.repo_root = Path(self.temp_dir)
        self.cache = StaticAnalysisCache(self.artifact_dir, self.repo_root)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_returns_none_for_missing_artifact(self):
        result = self.cache.get()
        self.assertIsNone(result)

    def test_save_creates_artifact_directory(self):
        self.assertFalse(self.artifact_dir.exists())

        results = StaticAnalysisResults()
        self.cache.save(results)

        self.assertTrue(self.artifact_dir.exists())

    def test_save_and_get_roundtrip(self):
        file1 = str(self.repo_root / "src/main.py")
        file2 = str(self.repo_root / "src/utils.py")
        results = StaticAnalysisResults()
        results.add_source_files("python", [file1, file2])

        self.cache.save(results)
        loaded = self.cache.get()

        self.assertIsNotNone(loaded)
        if loaded is None:
            return

        self.assertEqual(loaded.get_source_files("python"), [file1, file2])

    def test_get_returns_none_for_corrupted_artifact(self):
        self.artifact_dir.mkdir(parents=True)
        (self.artifact_dir / STATIC_ANALYSIS_PKL).write_bytes(b"not a valid pickle")

        result = self.cache.get()
        self.assertIsNone(result)

    def test_save_overwrites_existing_artifact(self):
        old_file = str(self.repo_root / "old.py")
        new_file = str(self.repo_root / "new.py")

        results1 = StaticAnalysisResults()
        results1.add_source_files("python", [old_file])
        self.cache.save(results1)

        results2 = StaticAnalysisResults()
        results2.add_source_files("python", [new_file])
        self.cache.save(results2)

        loaded = self.cache.get()
        self.assertIsNotNone(loaded)
        if loaded is None:
            return

        self.assertEqual(loaded.get_source_files("python"), [new_file])

    def test_artifact_filenames(self):
        results = StaticAnalysisResults()
        self.cache.save(results, source_sha="abc123")

        self.assertTrue((self.artifact_dir / STATIC_ANALYSIS_PKL).exists())
        self.assertTrue((self.artifact_dir / STATIC_ANALYSIS_SHA).exists())


class TestStaticAnalysisCacheShaGate(unittest.TestCase):
    """Tests for the SHA-tag gate added in Shape Y phase 1."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.artifact_dir = Path(self.temp_dir) / ".codeboarding"
        self.repo_root = Path(self.temp_dir)
        self.cache = StaticAnalysisCache(self.artifact_dir, self.repo_root)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sha_match_returns_results(self):
        results = StaticAnalysisResults()
        results.add_source_files("python", [str(self.repo_root / "main.py")])
        self.cache.save(results, source_sha="sha-current")

        loaded = self.cache.get(expected_sha="sha-current")
        self.assertIsNotNone(loaded)

    def test_sha_mismatch_returns_none(self):
        results = StaticAnalysisResults()
        self.cache.save(results, source_sha="sha-old")

        loaded = self.cache.get(expected_sha="sha-new")
        self.assertIsNone(loaded)

    def test_missing_tag_with_expected_sha_returns_none(self):
        results = StaticAnalysisResults()
        self.cache.save(results, source_sha=None)

        loaded = self.cache.get(expected_sha="sha-current")
        self.assertIsNone(loaded)

    def test_missing_tag_without_expected_sha_returns_results(self):
        # Untagged save then untagged load = still works (legacy / CLI path).
        results = StaticAnalysisResults()
        results.add_source_files("python", [str(self.repo_root / "main.py")])
        self.cache.save(results, source_sha=None)

        loaded = self.cache.get(expected_sha=None)
        self.assertIsNotNone(loaded)

    def test_resave_without_sha_drops_stale_tag(self):
        # First save with tag, second save without tag should drop the old tag
        # so a SHA-gated load doesn't accept a now-mismatched pickle.
        results = StaticAnalysisResults()
        self.cache.save(results, source_sha="sha-old")
        self.assertTrue((self.artifact_dir / STATIC_ANALYSIS_SHA).exists())

        self.cache.save(results, source_sha=None)
        self.assertFalse((self.artifact_dir / STATIC_ANALYSIS_SHA).exists())

    def test_unknown_tag_version_treated_as_miss(self):
        results = StaticAnalysisResults()
        self.cache.save(results, source_sha="sha-current")
        # Manually rewrite the tag with an unknown version prefix.
        (self.artifact_dir / STATIC_ANALYSIS_SHA).write_text("v999\nsha-current\n")

        loaded = self.cache.get(expected_sha="sha-current")
        self.assertIsNone(loaded)


class TestStaticAnalysisCacheLegacyMigration(unittest.TestCase):
    """One-time fallback for pickles written by the previous on-disk layout."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.artifact_dir = Path(self.temp_dir) / ".codeboarding"
        self.repo_root = Path(self.temp_dir)
        self.cache = StaticAnalysisCache(self.artifact_dir, self.repo_root)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_reads_legacy_cache_when_new_artifact_absent(self):
        # Seed a legacy-shaped pickle by writing through the new save path
        # then moving the file to the old location, so the on-disk bytes are
        # still loadable but live where the previous code would have written.
        results = StaticAnalysisResults()
        results.add_source_files("python", [str(self.repo_root / "src/legacy.py")])
        self.cache.save(results, source_sha=None)
        new_pkl = self.artifact_dir / STATIC_ANALYSIS_PKL

        legacy_dir = self.artifact_dir / "cache"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        legacy_pkl = legacy_dir / "static_analysis_results.pkl"
        new_pkl.replace(legacy_pkl)

        loaded = self.cache.get(expected_sha=None)
        self.assertIsNotNone(loaded)
        if loaded is None:
            return
        self.assertEqual(
            loaded.get_source_files("python"),
            [str(self.repo_root / "src/legacy.py")],
        )

    def test_legacy_fallback_disabled_under_sha_gate(self):
        # SHA-gated callers must not silently accept untagged legacy pickles.
        results = StaticAnalysisResults()
        self.cache.save(results, source_sha=None)
        new_pkl = self.artifact_dir / STATIC_ANALYSIS_PKL

        legacy_dir = self.artifact_dir / "cache"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        legacy_pkl = legacy_dir / "static_analysis_results.pkl"
        new_pkl.replace(legacy_pkl)

        loaded = self.cache.get(expected_sha="anything")
        self.assertIsNone(loaded)


class TestStaticAnalysisCacheAtomicWrite(unittest.TestCase):
    """Tests for atomic write behavior of StaticAnalysisCache."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.artifact_dir = Path(self.temp_dir) / ".codeboarding"
        self.repo_root = Path(self.temp_dir)
        self.cache = StaticAnalysisCache(self.artifact_dir, self.repo_root)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_no_temp_files_after_save(self):
        results = StaticAnalysisResults()
        self.cache.save(results, source_sha="sha-current")

        tmp_files = list(self.artifact_dir.glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0)


class TestStaticAnalysisCacheReadTagSha(unittest.TestCase):
    """Public ``read_tag_sha`` mirrors the SHA-gate's internal version check."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.artifact_dir = Path(self.temp_dir) / ".codeboarding"
        self.repo_root = Path(self.temp_dir)
        self.cache = StaticAnalysisCache(self.artifact_dir, self.repo_root)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_returns_none_when_tag_missing(self):
        self.assertIsNone(self.cache.read_tag_sha())

    def test_returns_sha_after_tagged_save(self):
        self.cache.save(StaticAnalysisResults(), source_sha="sha-current")
        self.assertEqual(self.cache.read_tag_sha(), "sha-current")

    def test_unknown_version_returns_none(self):
        # Why: the wrapper's restore path SHA-gates with this method; if Core
        # bumps the tag format, the wrapper must miss the cache rather than
        # silently restoring an incompatible pickle.
        self.artifact_dir.mkdir(parents=True)
        (self.artifact_dir / STATIC_ANALYSIS_SHA).write_text("v999\nsha-current\n")
        self.assertIsNone(self.cache.read_tag_sha())


class TestLoadWithSha(unittest.TestCase):
    """``load_with_sha`` returns the unpickled cache plus the tag SHA, as a pair.

    Used by the warm-start flow: the SHA is needed as a git diff base, not
    as an exact-match gate, so the loader hands back whatever's on disk
    along with its tag value.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.artifact_dir = Path(self.temp_dir) / ".codeboarding"
        self.repo_root = Path(self.temp_dir)
        self.cache = StaticAnalysisCache(self.artifact_dir, self.repo_root)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_returns_results_and_sha_when_both_present(self):
        results = StaticAnalysisResults()
        results.add_source_files("python", [str(self.repo_root / "main.py")])
        self.cache.save(results, source_sha="sha-current")

        loaded = self.cache.load_with_sha()

        self.assertIsNotNone(loaded)
        if loaded is not None:
            cached_results, cached_sha = loaded
            self.assertEqual(cached_sha, "sha-current")
            self.assertEqual(cached_results.get_source_files("python"), [str(self.repo_root / "main.py")])

    def test_returns_none_when_tag_absent(self):
        # Untagged save: pkl exists but no SHA tag -> not warm-startable.
        results = StaticAnalysisResults()
        self.cache.save(results, source_sha=None)

        self.assertIsNone(self.cache.load_with_sha())

    def test_returns_none_when_pkl_absent(self):
        self.assertIsNone(self.cache.load_with_sha())

    def test_does_not_gate_on_caller_supplied_sha(self):
        # Even though the cached SHA is "sha-A", the load succeeds; the caller
        # uses the returned SHA as a git diff base, not for equality matching.
        self.cache.save(StaticAnalysisResults(), source_sha="sha-A")

        loaded = self.cache.load_with_sha()

        self.assertIsNotNone(loaded)
        if loaded is not None:
            _, cached_sha = loaded
            self.assertEqual(cached_sha, "sha-A")


class TestCopyCacheFiles(unittest.TestCase):
    """``copy_cache_files`` atomically copies the pkl + sha pair between dirs."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_root = Path(self.temp_dir)
        self.src_dir = Path(self.temp_dir) / "src" / ".codeboarding"
        self.dst_dir = Path(self.temp_dir) / "dst" / ".codeboarding"
        self.src_cache = StaticAnalysisCache(self.src_dir, self.repo_root)
        self.dst_cache = StaticAnalysisCache(self.dst_dir, self.repo_root)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_copies_both_files_and_returns_true(self):
        results = StaticAnalysisResults()
        results.add_source_files("python", [str(self.repo_root / "main.py")])
        self.src_cache.save(results, source_sha="sha-current")

        self.assertTrue(copy_cache_files(self.src_dir, self.dst_dir))
        self.assertTrue((self.dst_dir / STATIC_ANALYSIS_PKL).exists())
        self.assertTrue((self.dst_dir / STATIC_ANALYSIS_SHA).exists())
        self.assertEqual(self.dst_cache.read_tag_sha(), "sha-current")

    def test_copied_pkl_is_loadable(self):
        results = StaticAnalysisResults()
        results.add_source_files("python", [str(self.repo_root / "main.py")])
        self.src_cache.save(results, source_sha="sha-current")

        copy_cache_files(self.src_dir, self.dst_dir)
        loaded = self.dst_cache.get(expected_sha="sha-current")
        self.assertIsNotNone(loaded)
        if loaded is not None:
            self.assertEqual(loaded.get_source_files("python"), [str(self.repo_root / "main.py")])

    def test_missing_pair_returns_false_without_changes(self):
        # No source pkl/sha at all -> no-op, no destination state.
        self.assertFalse(copy_cache_files(self.src_dir, self.dst_dir))
        self.assertFalse(self.dst_dir.exists() and any(self.dst_dir.iterdir()))

    def test_partial_source_refuses_copy(self):
        # Pkl present, sha absent -> refuse to copy (would leave dst with a
        # pickle but no SHA gate, which a SHA-aware reader treats as no-cache
        # but a tag-less reader may load a stale snapshot).
        self.src_dir.mkdir(parents=True)
        (self.src_dir / STATIC_ANALYSIS_PKL).write_bytes(b"placeholder")

        self.assertFalse(copy_cache_files(self.src_dir, self.dst_dir))
        self.assertFalse((self.dst_dir / STATIC_ANALYSIS_PKL).exists())
        self.assertFalse((self.dst_dir / STATIC_ANALYSIS_SHA).exists())

    def test_overwrites_existing_destination(self):
        old_results = StaticAnalysisResults()
        old_results.add_source_files("python", [str(self.repo_root / "old.py")])
        self.dst_cache.save(old_results, source_sha="sha-old")

        new_results = StaticAnalysisResults()
        new_results.add_source_files("python", [str(self.repo_root / "new.py")])
        self.src_cache.save(new_results, source_sha="sha-new")

        self.assertTrue(copy_cache_files(self.src_dir, self.dst_dir))
        self.assertEqual(self.dst_cache.read_tag_sha(), "sha-new")
        loaded = self.dst_cache.get(expected_sha="sha-new")
        self.assertIsNotNone(loaded)
        if loaded is not None:
            self.assertEqual(loaded.get_source_files("python"), [str(self.repo_root / "new.py")])


if __name__ == "__main__":
    unittest.main()
