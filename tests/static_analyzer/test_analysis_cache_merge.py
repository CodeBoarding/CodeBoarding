"""Regression tests for ``AnalysisCacheManager.merge_results``.

The merge layer sits between two parts of the codebase that disagree on
the type of ``source_files``:

  - The engine declares ``LanguageAnalysisResult.source_files`` as
    ``list[str]`` and ``call_graph_builder.py`` explicitly produces
    strings (``str(f.resolve())``).
  - The cache load path (``analysis_cache.py:load`` / ``parser.py``) returns
    ``list[Path]``.

If ``merge_results`` accepts both inputs verbatim, the merged
``source_files`` ends up as a mixed list — and downstream consumers like
``incremental_orchestrator.py:294`` (``{f for f in source_files if
f.exists()}``) crash with ``AttributeError: 'str' object has no
attribute 'exists'`` on the first ``str`` element. The orchestrator
catches that and falls back to a full analysis, paying ~4s of duplicate
work on every incremental refresh.

The contract these tests pin: after merge, every ``source_files`` entry
is a ``Path``, regardless of which side it came from.
"""

import unittest
from pathlib import Path

from static_analyzer.analysis_cache import AnalysisCacheManager
from static_analyzer.graph import CallGraph


def _empty_lang_dict(source_files: list) -> dict:
    """Minimal dict in the merge_results contract shape."""
    return {
        "call_graph": CallGraph(language="typescript"),
        "class_hierarchies": {},
        "package_relations": {},
        "references": [],
        "source_files": source_files,
        "diagnostics": {},
    }


class TestMergeResultsSourceFilesAreUniformPath(unittest.TestCase):
    def setUp(self) -> None:
        self.mgr = AnalysisCacheManager(repo_root=Path("/repo"))

    def test_str_inputs_become_path(self) -> None:
        cached = _empty_lang_dict([Path("/repo/a.ts"), Path("/repo/c.ts")])
        new = _empty_lang_dict(["/repo/b.ts", "/repo/d.ts"])

        merged = self.mgr.merge_results(cached, new)

        for entry in merged["source_files"]:
            self.assertIsInstance(entry, Path, f"got {type(entry).__name__} for {entry}")

    def test_filter_by_exists_does_not_crash(self) -> None:
        """Mirrors the exact line at incremental_orchestrator.py:294 that
        used to AttributeError on mixed input."""
        cached = _empty_lang_dict([Path("/repo/a.ts"), Path("/repo/c.ts")])
        new = _empty_lang_dict(["/repo/b.ts", "/repo/d.ts"])

        merged = self.mgr.merge_results(cached, new)

        # No crash even though the files don't physically exist; .exists()
        # is what we care about — it must not AttributeError.
        result = {f for f in merged["source_files"] if f.exists()}
        self.assertIsInstance(result, set)

    def test_paths_on_both_sides_still_path(self) -> None:
        cached = _empty_lang_dict([Path("/repo/a.ts")])
        new = _empty_lang_dict([Path("/repo/b.ts")])
        merged = self.mgr.merge_results(cached, new)
        for entry in merged["source_files"]:
            self.assertIsInstance(entry, Path)

    def test_strs_on_both_sides_still_path(self) -> None:
        cached = _empty_lang_dict(["/repo/a.ts"])
        new = _empty_lang_dict(["/repo/b.ts"])
        merged = self.mgr.merge_results(cached, new)
        for entry in merged["source_files"]:
            self.assertIsInstance(entry, Path)

    def test_dedupes_overlapping_files_kept_once(self) -> None:
        """Sanity: a file present on both sides should appear once in the
        merged list (the new side wins for that file's content; the
        cached duplicate is dropped)."""
        cached = _empty_lang_dict([Path("/repo/a.ts"), Path("/repo/b.ts")])
        new = _empty_lang_dict(["/repo/b.ts"])  # b.ts reanalyzed
        merged = self.mgr.merge_results(cached, new)

        as_str = sorted(str(p) for p in merged["source_files"])
        self.assertEqual(as_str, ["/repo/a.ts", "/repo/b.ts"])

    def test_empty_inputs(self) -> None:
        cached = _empty_lang_dict([])
        new = _empty_lang_dict([])
        merged = self.mgr.merge_results(cached, new)
        self.assertEqual(merged["source_files"], [])


if __name__ == "__main__":
    unittest.main()
