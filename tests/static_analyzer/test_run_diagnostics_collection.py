"""What the static-analysis pass records when a language does not make it into the graph."""

import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from run_diagnostics import RunDiagnostics
from run_diagnostics.catalog import language_engine_failed
from static_analyzer import EngineConfig, StaticAnalyzer
from static_analyzer.analysis_cache import StaticAnalysisCache
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.scanner import UNSUPPORTED_CODE_LANGUAGES
from utils import CODEBOARDING_DIR_NAME


def _engine_config(language: str, project_path: Path, source_files: list[Path]) -> EngineConfig:
    adapter = MagicMock()
    adapter.language = language
    adapter.discover_source_files = MagicMock(return_value=source_files)
    return EngineConfig(adapter, project_path, source_files=list(source_files))


class TestStartClientsDiagnostics(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.analyzer = StaticAnalyzer.__new__(StaticAnalyzer)
        self.analyzer.repository_path = self.temp_dir
        self.analyzer.ignore_manager = MagicMock()
        self.analyzer.run_diagnostics = RunDiagnostics()
        self.analyzer._prepared_projects = {}
        self.analyzer._prepared_lock = threading.Lock()
        self.analyzer._clients_started = False
        self.analyzer._engine_clients = []

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _codes(self) -> dict[str, str]:
        return {entry.code: entry.subject for entry in self.analyzer.run_diagnostics.entries()}

    def test_a_server_that_never_starts_is_reported_against_its_language(self):
        self.analyzer._engine_configs = [
            _engine_config("CSharp", self.temp_dir, [self.temp_dir / "A.cs"]),
            _engine_config("Python", self.temp_dir, [self.temp_dir / "a.py"]),
        ]
        self.analyzer._spawn_engine_client = MagicMock(side_effect=[RuntimeError("csharp-ls missing"), MagicMock()])

        self.analyzer.start_clients()

        entries = self.analyzer.run_diagnostics.entries()
        self.assertEqual([e.code for e in entries], ["static.language_server_unavailable"])
        self.assertEqual(entries[0].subject, "CSharp")
        self.assertIn("csharp-ls missing", entries[0].detail)

    def test_a_language_with_nothing_to_index_anywhere_is_reported_once(self):
        self.analyzer._engine_configs = [
            _engine_config("TypeScript", self.temp_dir / "a", []),
            _engine_config("TypeScript", self.temp_dir / "b", []),
            _engine_config("Python", self.temp_dir, [self.temp_dir / "a.py"]),
        ]
        self.analyzer._spawn_engine_client = MagicMock(return_value=MagicMock())

        self.analyzer.start_clients()

        self.assertEqual(self._codes(), {"static.no_source_files": "TypeScript"})

    def test_one_empty_project_in_a_monorepo_is_not_a_hole(self):
        """A language that indexed somewhere is in the graph; saying otherwise is noise."""
        self.analyzer._engine_configs = [
            _engine_config("CSharp", self.temp_dir / "a", []),
            _engine_config("CSharp", self.temp_dir / "b", [self.temp_dir / "b" / "A.cs"]),
        ]
        self.analyzer._spawn_engine_client = MagicMock(return_value=MagicMock())

        self.analyzer.start_clients()

        self.assertEqual(self._codes(), {})


class TestUnsupportedCodeLanguages(unittest.TestCase):
    def test_data_formats_are_not_treated_as_unanalyzed_code(self):
        """Tokei counts JSON and Markdown as languages; reporting them would be nonsense."""
        for technology in ("JSON", "YAML", "Markdown", "Plain Text", "Shell", "TOML", "HTML", "CSS"):
            self.assertNotIn(technology, UNSUPPORTED_CODE_LANGUAGES)

    def test_real_languages_without_a_server_are_reportable(self):
        for technology in ("Kotlin", "Ruby", "Swift", "C++"):
            self.assertIn(technology, UNSUPPORTED_CODE_LANGUAGES)


class TestDiagnosticsAreNotCached(unittest.TestCase):
    def test_the_pickle_never_carries_a_previous_run_report(self):
        temp_dir = Path(tempfile.mkdtemp())
        try:
            cache = StaticAnalysisCache(temp_dir / CODEBOARDING_DIR_NAME, temp_dir)
            results = StaticAnalysisResults()
            results.run_diagnostics.record(language_engine_failed("Go", "boom"))

            cache.save(results, source_sha="sha")
            loaded = cache.get()

            assert loaded is not None
            self.assertEqual(loaded.run_diagnostics.entries(), [])
            # The live object keeps its own; only the artifact is stripped.
            self.assertEqual(len(results.run_diagnostics.entries()), 1)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
