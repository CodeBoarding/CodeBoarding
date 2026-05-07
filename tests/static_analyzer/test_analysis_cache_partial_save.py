"""Partial-save rollback: if the SHA tag write fails after the pkl is in
place, the cache must self-purge the now-stale tag rather than leave a
mismatched pair on disk.

Why this matters: the prior tag may point at SHA-A; the just-renamed pkl
contains SHA-B's bytes. A subsequent ``get(expected_sha="sha-A")`` would
unpickle SHA-B's call graph and return it as if it were SHA-A. Silent wrong
data is worse than a cache miss — the contract here is that a tag-write
failure degrades to a guaranteed miss.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from static_analyzer.analysis_cache import (
    STATIC_ANALYSIS_PKL,
    STATIC_ANALYSIS_SHA,
    StaticAnalysisCache,
)
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Language


class TestPartialSaveRollback(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.artifact_dir = Path(self.temp_dir) / ".codeboarding"
        self.repo_root = Path(self.temp_dir)
        self.cache = StaticAnalysisCache(self.artifact_dir, self.repo_root)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _seed(self, file_name: str, sha: str) -> StaticAnalysisResults:
        results = StaticAnalysisResults()
        results.add_source_files(Language.PYTHON, [str(self.repo_root / file_name)])
        self.cache.save(results, source_sha=sha)
        return results

    def test_tag_write_failure_drops_tag_to_force_cache_miss(self):
        # Seed: cache has pkl + tag for sha-A.
        self._seed("a.py", "sha-A")
        self.assertEqual(self.cache.read_tag_sha(), "sha-A")

        # New save with sha-B: replace the *tag's* rename with a failure. The
        # pkl rename runs first and succeeds, so the on-disk pkl is now sha-B's
        # but the only tag-write attempt fails.
        results_b = StaticAnalysisResults()
        results_b.add_source_files(Language.PYTHON, [str(self.repo_root / "b.py")])

        original_replace = Path.replace
        sha_path = self.artifact_dir / STATIC_ANALYSIS_SHA

        def _selective_replace(self_path: Path, target: Path):
            if Path(target) == sha_path:
                raise OSError("simulated tag rename failure")
            return original_replace(self_path, target)

        with patch.object(Path, "replace", _selective_replace):
            self.cache.save(results_b, source_sha="sha-B")

        # pkl is in place (the new bytes).
        self.assertTrue((self.artifact_dir / STATIC_ANALYSIS_PKL).exists())
        # The stale tag was purged — without rollback this would still say sha-A,
        # which paired with sha-B's pkl is a silent wrong-data hazard.
        self.assertFalse(sha_path.exists())

        # Concretely: a SHA-A reader that previously found a hit must now miss.
        self.assertIsNone(self.cache.get(expected_sha="sha-A"))
        # And a SHA-B reader misses too (no tag), which is the safe fallback.
        self.assertIsNone(self.cache.get(expected_sha="sha-B"))
        # Untagged read still works (the pkl is fine).
        loaded = self.cache.get(expected_sha=None)
        self.assertIsNotNone(loaded)

    def test_tag_write_failure_on_first_save_leaves_no_tag(self):
        # No prior state: the first save's tag rename fails. We expect no tag
        # on disk and a usable (but tag-less) pkl.
        results = StaticAnalysisResults()
        results.add_source_files(Language.PYTHON, [str(self.repo_root / "first.py")])

        original_replace = Path.replace
        sha_path = self.artifact_dir / STATIC_ANALYSIS_SHA

        def _selective_replace(self_path: Path, target: Path):
            if Path(target) == sha_path:
                raise OSError("simulated tag rename failure")
            return original_replace(self_path, target)

        with patch.object(Path, "replace", _selective_replace):
            self.cache.save(results, source_sha="sha-first")

        self.assertTrue((self.artifact_dir / STATIC_ANALYSIS_PKL).exists())
        self.assertFalse(sha_path.exists())
        self.assertIsNone(self.cache.read_tag_sha())

    def test_no_temp_files_after_rollback(self):
        # Regression: rollback path must clean up its tag-temp file.
        self._seed("a.py", "sha-A")

        results_b = StaticAnalysisResults()
        results_b.add_source_files(Language.PYTHON, [str(self.repo_root / "b.py")])

        original_replace = Path.replace
        sha_path = self.artifact_dir / STATIC_ANALYSIS_SHA

        def _selective_replace(self_path: Path, target: Path):
            if Path(target) == sha_path:
                raise OSError("boom")
            return original_replace(self_path, target)

        with patch.object(Path, "replace", _selective_replace):
            self.cache.save(results_b, source_sha="sha-B")

        leftover = list(self.artifact_dir.glob("*.tmp"))
        self.assertEqual(leftover, [])


if __name__ == "__main__":
    unittest.main()
