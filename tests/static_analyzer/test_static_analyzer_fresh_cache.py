"""Tests for the LSP-free fresh-pkl fast path (``load_cache_if_fresh``).

Covers the contract that a SHA-tagged pkl with no relevant changed files is
served without starting any LSP client, while a relevant change, a git diff
failure, or the cache-reuse kill switch falls back to the normal LSP path.
"""

from pathlib import Path
from subprocess import CalledProcessError
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer import EngineConfig, StaticAnalyzer, get_static_analysis
from static_analyzer.analysis_cache import StaticAnalysisCache
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Language
from static_analyzer.engine.language_adapter import LanguageAdapter


def _make_adapter(
    language: str, extensions: tuple[str, ...], language_enum: Language = Language.PYTHON
) -> LanguageAdapter:
    adapter = MagicMock(name=f"{language}Adapter")
    adapter.language = language
    adapter.file_extensions = extensions
    adapter.language_enum = language_enum
    return cast(LanguageAdapter, adapter)


@pytest.fixture
def analyzer(tmp_path: Path) -> StaticAnalyzer:
    # Bypass ProjectScanner / config discovery — we inject _engine_configs directly.
    with patch("static_analyzer.ProjectScanner") as scanner_cls:
        scanner_cls.return_value.scan.return_value = []
        sa = StaticAnalyzer(tmp_path)
    sa._engine_configs = [EngineConfig(_make_adapter("Python", (".py",)), tmp_path)]
    return sa


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """Pkl tagged "abc123" with a Python bucket, so hits also prove bucket matching."""
    d = tmp_path / ".codeboarding"
    results = StaticAnalysisResults()
    results.add_source_files(Language.PYTHON, [str(tmp_path / "a.py")])
    StaticAnalysisCache(d, tmp_path).save(results, source_sha="abc123")
    return d


class TestLoadCacheIfFresh:
    def test_zero_changed_files_serves_pkl(self, analyzer: StaticAnalyzer, cache_dir: Path, tmp_path: Path) -> None:
        with patch("static_analyzer.get_changed_files_since", return_value=set()) as diff:
            result = analyzer.load_cache_if_fresh(cache_dir)

        assert result is not None
        diff.assert_called_once_with(tmp_path, "abc123")
        assert analyzer._cached_results is result
        assert analyzer._pending_cache_dir == cache_dir
        assert analyzer.collected_diagnostics == result.diagnostics

    def test_changed_source_file_misses(self, analyzer: StaticAnalyzer, cache_dir: Path, tmp_path: Path) -> None:
        """The path need not exist — deletions are relevant changes too."""
        with patch("static_analyzer.get_changed_files_since", return_value={tmp_path / "src" / "main.py"}):
            assert analyzer.load_cache_if_fresh(cache_dir) is None
        assert analyzer._cached_results is None

    def test_changed_non_source_files_still_hit(
        self, analyzer: StaticAnalyzer, cache_dir: Path, tmp_path: Path
    ) -> None:
        with patch(
            "static_analyzer.get_changed_files_since", return_value={tmp_path / "README.md", tmp_path / "Makefile"}
        ):
            assert analyzer.load_cache_if_fresh(cache_dir) is not None

    def test_changed_ignored_source_file_still_hits(
        self, analyzer: StaticAnalyzer, cache_dir: Path, tmp_path: Path
    ) -> None:
        with patch("static_analyzer.get_changed_files_since", return_value={tmp_path / ".venv" / "lib" / "site.py"}):
            assert analyzer.load_cache_if_fresh(cache_dir) is not None

    def test_git_diff_failure_misses(self, analyzer: StaticAnalyzer, cache_dir: Path) -> None:
        with patch("static_analyzer.get_changed_files_since", side_effect=CalledProcessError(128, "git")):
            assert analyzer.load_cache_if_fresh(cache_dir) is None

    def test_absent_pkl_misses(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        assert analyzer.load_cache_if_fresh(tmp_path / "no-cache-here") is None

    def test_no_engine_configs_misses(self, analyzer: StaticAnalyzer, cache_dir: Path) -> None:
        analyzer._engine_configs = []
        assert analyzer.load_cache_if_fresh(cache_dir) is None

    def test_kill_switch_misses(
        self, analyzer: StaticAnalyzer, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEBOARDING_DISABLE_CACHE_REUSE", "1")
        with patch("static_analyzer.get_changed_files_since", return_value=set()):
            assert analyzer.load_cache_if_fresh(cache_dir) is None

    def test_change_in_any_config_misses(self, analyzer: StaticAnalyzer, cache_dir: Path, tmp_path: Path) -> None:
        analyzer._engine_configs = [
            EngineConfig(_make_adapter("Python", (".py",)), tmp_path),
            EngineConfig(_make_adapter("TypeScript", (".ts", ".tsx"), Language.TYPESCRIPT), tmp_path),
        ]
        with patch("static_analyzer.get_changed_files_since", return_value={tmp_path / "app.ts"}):
            assert analyzer.load_cache_if_fresh(cache_dir) is None

    def test_removed_language_bucket_misses(self, analyzer: StaticAnalyzer, cache_dir: Path, tmp_path: Path) -> None:
        """A cached language with no current engine config has no diff to vouch for it — fall back."""
        analyzer._engine_configs = [EngineConfig(_make_adapter("TypeScript", (".ts",), Language.TYPESCRIPT), tmp_path)]
        with patch("static_analyzer.get_changed_files_since", return_value=set()):
            assert analyzer.load_cache_if_fresh(cache_dir) is None

    def test_concurrent_resave_with_different_sha_misses(self, analyzer: StaticAnalyzer, cache_dir: Path) -> None:
        """The SHA-gated unpickle misses when the pkl is re-saved between the tag probe and the load."""
        with patch.object(StaticAnalysisCache, "get", return_value=None):
            with patch("static_analyzer.get_changed_files_since", return_value=set()):
                assert analyzer.load_cache_if_fresh(cache_dir) is None

    def test_probe_error_falls_back_instead_of_raising(self, analyzer: StaticAnalyzer, cache_dir: Path) -> None:
        with patch.object(StaticAnalysisCache, "read_tag_sha", side_effect=TimeoutError("lock held")):
            assert analyzer.load_cache_if_fresh(cache_dir) is None

    def test_miss_does_not_unpickle(self, analyzer: StaticAnalyzer, cache_dir: Path, tmp_path: Path) -> None:
        """A miss caused by changed files must cost only the tag read + git diff, not the pkl load."""
        with patch.object(StaticAnalysisCache, "get") as load:
            with patch("static_analyzer.get_changed_files_since", return_value={tmp_path / "main.py"}):
                assert analyzer.load_cache_if_fresh(cache_dir) is None
        load.assert_not_called()

    def test_memoized_results_win_over_disk(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        """Same contract as ``load_from_disk_cache``: an in-memory result is never replaced by a re-unpickle."""
        preset = StaticAnalysisResults()
        analyzer._cached_results = preset
        assert analyzer.load_cache_if_fresh(tmp_path / "no-cache-here") is preset


class TestAnalyzeFreshCacheFastPath:
    def test_analyze_without_clients_serves_fresh_pkl(self, analyzer: StaticAnalyzer, cache_dir: Path) -> None:
        with patch("static_analyzer.get_changed_files_since", return_value=set()):
            result = analyzer.analyze(cache_dir=cache_dir, source_sha="def456")

        assert result is analyzer._cached_results
        assert analyzer._pending_source_sha == "def456"
        assert analyzer._pending_cache_dir == cache_dir
        assert analyzer._clients_started is False

    def test_analyze_without_clients_stale_pkl_raises(
        self, analyzer: StaticAnalyzer, cache_dir: Path, tmp_path: Path
    ) -> None:
        with patch("static_analyzer.get_changed_files_since", return_value={tmp_path / "x.py"}):
            with pytest.raises(RuntimeError, match="LSP clients are not running"):
                analyzer.analyze(cache_dir=cache_dir)

    def test_analyze_without_clients_skip_cache_raises(self, analyzer: StaticAnalyzer, cache_dir: Path) -> None:
        with patch("static_analyzer.get_changed_files_since", return_value=set()):
            with pytest.raises(RuntimeError, match="LSP clients are not running"):
                analyzer.analyze(cache_dir=cache_dir, skip_cache=True)


class TestGetStaticAnalysisFastPath:
    def test_fresh_hit_never_starts_clients(self, tmp_path: Path) -> None:
        sentinel = StaticAnalysisResults()
        with patch("static_analyzer.StaticAnalyzer") as analyzer_cls:
            instance = analyzer_cls.return_value
            instance.load_cache_if_fresh.return_value = sentinel
            result = get_static_analysis(tmp_path, cache_dir=tmp_path / ".codeboarding")

        assert result is sentinel
        instance.__enter__.assert_not_called()
        instance.analyze.assert_not_called()

    def test_miss_falls_back_to_lsp_lifecycle(self, tmp_path: Path) -> None:
        with patch("static_analyzer.StaticAnalyzer") as analyzer_cls:
            instance = analyzer_cls.return_value
            instance.load_cache_if_fresh.return_value = None
            get_static_analysis(tmp_path, cache_dir=tmp_path / ".codeboarding")

        instance.__enter__.assert_called_once()
        instance.analyze.assert_called_once()

    def test_skip_cache_bypasses_fast_path(self, tmp_path: Path) -> None:
        with patch("static_analyzer.StaticAnalyzer") as analyzer_cls:
            instance = analyzer_cls.return_value
            get_static_analysis(tmp_path, cache_dir=tmp_path / ".codeboarding", skip_cache=True)

        instance.load_cache_if_fresh.assert_not_called()
        instance.analyze.assert_called_once()
