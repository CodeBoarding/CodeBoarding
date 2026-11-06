"""Tests for ``StaticAnalyzer.start_clients`` graceful-degradation behavior.

Covers the failure-mode contract introduced for issue #280: a single
language's LSP client failing to start must not tear down the other
clients, and a total failure must raise a ``RuntimeError`` listing all
attempted languages.
"""

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer import EngineConfig, StaticAnalysisFatalError, StaticAnalyzer
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.config import Language
from static_analyzer.engine.adapters.java_adapter import JavaAdapter
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.exceptions import WorkspaceSetupError


def _make_adapter(
    language: str,
    *,
    wait_for_workspace_ready: bool = False,
    language_enum: Language | None = None,
    fail_on_empty_symbols: bool = False,
) -> LanguageAdapter:
    adapter = MagicMock(name=f"{language}Adapter")
    adapter.language = language
    if language_enum is not None:
        adapter.language_enum = language_enum
        # Real adapters default results_language to language_enum; a MagicMock would not.
        adapter.results_language = language_enum
    adapter.fail_on_empty_symbols = fail_on_empty_symbols
    adapter.get_lsp_command.return_value = [f"{language.lower()}-lsp"]
    adapter.get_lsp_init_options.return_value = {}
    adapter.get_lsp_env.return_value = {}
    adapter.get_workspace_settings.return_value = {}
    adapter.wait_for_workspace_ready = wait_for_workspace_ready
    adapter.workspace_folders.return_value = []
    adapter.fallback_workspace_folders.return_value = []
    adapter.lift_import_filters.return_value = False
    return cast(LanguageAdapter, adapter)


@pytest.fixture
def analyzer(tmp_path: Path) -> StaticAnalyzer:
    # Bypass ProjectScanner / config discovery — we inject _engine_configs directly.
    with patch("static_analyzer.ProjectScanner") as scanner_cls:
        scanner_cls.return_value.scan.return_value = []
        sa = StaticAnalyzer(tmp_path)
    return sa


class TestStartClientsSkipsEmptyLanguages:
    """A detected language with no files of its own must not cost an LSP process.

    The scanner flags a language from a handful of stray files, and starting its
    server (tsserver plus its node workers, ~350MB) to then analyze nothing is
    pure overhead held for the whole run.
    """

    def test_language_without_source_files_starts_no_client(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        py_adapter = _make_adapter("Python")
        py_adapter.discover_source_files.return_value = [tmp_path / "a.py"]  # type: ignore[attr-defined]
        js_adapter = _make_adapter("JavaScript")
        js_adapter.discover_source_files.return_value = []  # type: ignore[attr-defined]
        analyzer._engine_configs = [EngineConfig(py_adapter, tmp_path), EngineConfig(js_adapter, tmp_path)]

        with patch("static_analyzer.LSPClient", side_effect=[MagicMock(name="PythonClient")]) as client_cls:
            analyzer.start_clients()

        assert client_cls.call_count == 1
        assert [c.adapter.language for c, _ in analyzer._engine_clients] == ["Python"]

    def test_discovered_files_are_reused_not_rewalked(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        adapter = _make_adapter("Python")
        discovered = [tmp_path / "a.py"]
        adapter.discover_source_files.return_value = discovered  # type: ignore[attr-defined]
        config = EngineConfig(adapter, tmp_path)
        analyzer._engine_configs = [config]

        with patch("static_analyzer.LSPClient", side_effect=[MagicMock()]):
            analyzer.start_clients()

        assert config.source_files == discovered

    def test_no_language_with_files_is_not_a_failure(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        adapter = _make_adapter("JavaScript")
        adapter.discover_source_files.return_value = []  # type: ignore[attr-defined]
        analyzer._engine_configs = [EngineConfig(adapter, tmp_path)]

        with patch("static_analyzer.LSPClient") as client_cls:
            analyzer.start_clients()  # must not raise: nothing to analyze != failure

        client_cls.assert_not_called()
        assert analyzer._clients_started is True
        assert analyzer._engine_clients == []


class TestStartClientsGracefulDegradation:
    def test_partial_failure_skips_failing_language_and_continues(
        self, analyzer: StaticAnalyzer, tmp_path: Path
    ) -> None:
        py_adapter = _make_adapter("Python")
        cs_adapter = _make_adapter("CSharp")
        ts_adapter = _make_adapter("TypeScript")
        analyzer._engine_configs = [
            EngineConfig(py_adapter, tmp_path),
            EngineConfig(cs_adapter, tmp_path),
            EngineConfig(ts_adapter, tmp_path),
        ]

        good_client_py = MagicMock(name="PythonClient")
        bad_client_cs = MagicMock(name="CSharpClient")
        bad_client_cs.start.side_effect = TimeoutError("OmniSharp timed out")
        good_client_ts = MagicMock(name="TypeScriptClient")

        with patch(
            "static_analyzer.LSPClient",
            side_effect=[good_client_py, bad_client_cs, good_client_ts],
        ):
            analyzer.start_clients()

        assert analyzer._clients_started is True
        assert [c.adapter.language for c, _ in analyzer._engine_clients] == ["Python", "TypeScript"]
        # Healthy clients must NOT be shut down because a sibling failed.
        good_client_py.shutdown.assert_not_called()
        good_client_ts.shutdown.assert_not_called()
        # Failing client gets a best-effort shutdown for partial-state cleanup.
        bad_client_cs.shutdown.assert_called_once()

    def test_a_workspace_that_cannot_be_set_up_fails_the_run_instead_of_skipping_the_language(
        self, analyzer: StaticAnalyzer, tmp_path: Path
    ) -> None:
        py_adapter = _make_adapter("Python")
        ts_adapter = _make_adapter("TypeScript")
        cast(MagicMock, ts_adapter).get_lsp_commands.side_effect = WorkspaceSetupError("unreadable pnpm-workspace.yaml")
        analyzer._engine_configs = [EngineConfig(py_adapter, tmp_path), EngineConfig(ts_adapter, tmp_path)]

        with patch("static_analyzer.LSPClient") as client_cls, pytest.raises(WorkspaceSetupError):
            analyzer.start_clients()

        client_cls.return_value.shutdown.assert_called_once()  # Python's, started before TypeScript failed

    def test_total_failure_raises_runtime_error_listing_attempted_languages(
        self, analyzer: StaticAnalyzer, tmp_path: Path
    ) -> None:
        py_adapter = _make_adapter("Python")
        cs_adapter = _make_adapter("CSharp")
        analyzer._engine_configs = [EngineConfig(py_adapter, tmp_path), EngineConfig(cs_adapter, tmp_path)]

        bad_py = MagicMock()
        bad_py.start.side_effect = RuntimeError("pyright missing")
        bad_cs = MagicMock()
        bad_cs.start.side_effect = TimeoutError("omnisharp timed out")

        with patch("static_analyzer.LSPClient", side_effect=[bad_py, bad_cs]):
            with pytest.raises(RuntimeError, match=r"attempted:.*Python.*CSharp.*pyright missing") as exc:
                analyzer.start_clients()

        assert "omnisharp timed out" in str(exc.value)
        assert analyzer._clients_started is False
        assert analyzer._engine_clients == []

    def test_validate_rejects_empty_symbol_csharp_result(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        cs_adapter = _make_adapter("CSharp", language_enum=Language.CSHARP, fail_on_empty_symbols=True)
        analyzer._engine_configs = [EngineConfig(cs_adapter, tmp_path)]

        results = StaticAnalysisResults()
        results.add_source_files(Language.CSHARP, [str(tmp_path / "Program.cs")])

        with pytest.raises(StaticAnalysisFatalError, match="0 symbols"):
            analyzer._validate_analysis_results(results)

    def test_validate_ignores_empty_non_opted_language(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        py_adapter = _make_adapter("Python", language_enum=Language.PYTHON, fail_on_empty_symbols=False)
        analyzer._engine_configs = [EngineConfig(py_adapter, tmp_path)]

        results = StaticAnalysisResults()
        results.add_source_files(Language.PYTHON, [str(tmp_path / "app.py")])

        analyzer._validate_analysis_results(results)

    @pytest.mark.parametrize("language", [Language.CPP, Language.CSHARP])
    @pytest.mark.parametrize("failure_type", [FileNotFoundError, TimeoutError, RuntimeError])
    def test_analysis_skips_failed_language_and_preserves_other_results(
        self,
        analyzer: StaticAnalyzer,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        language: Language,
        failure_type: type[Exception],
    ) -> None:
        adapter = _make_adapter(language.value, language_enum=language, fail_on_empty_symbols=True)
        py_adapter = _make_adapter("Python", language_enum=Language.PYTHON)
        analyzer._engine_configs = [
            EngineConfig(adapter, tmp_path, source_files=[tmp_path / "source"]),
            EngineConfig(py_adapter, tmp_path, source_files=[tmp_path / "helper.py"]),
        ]
        analyzer._server_slots = 1
        analyzer._clients_started = True
        with patch.object(
            analyzer,
            "_analyse_config",
            side_effect=[
                failure_type("engine unavailable"),
                [(py_adapter, {"source_files": [tmp_path / "helper.py"]})],
            ],
        ):
            result = analyzer.analyze(cache_dir=tmp_path, skip_cache=True)
        assert result.get_languages() == [Language.PYTHON]
        assert result.get_source_files(Language.PYTHON) == [str(tmp_path / "helper.py")]
        assert "engine unavailable" in caplog.text

    def test_explicit_fatal_error_preserves_failure(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        adapter = _make_adapter("CPP", language_enum=Language.CPP, fail_on_empty_symbols=True)
        analyzer._engine_configs = [EngineConfig(adapter, tmp_path, source_files=[tmp_path / "main.cpp"])]
        analyzer._server_slots = 1
        analyzer._clients_started = True
        failure = StaticAnalysisFatalError("server process exited")
        with patch.object(analyzer, "_analyse_config", side_effect=failure):
            with pytest.raises(StaticAnalysisFatalError, match="server process exited") as error:
                analyzer.analyze(cache_dir=tmp_path, skip_cache=True)
        assert error.value is failure

    def test_all_success_records_no_failures(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        py_adapter = _make_adapter("Python")
        ts_adapter = _make_adapter("TypeScript")
        analyzer._engine_configs = [EngineConfig(py_adapter, tmp_path), EngineConfig(ts_adapter, tmp_path)]

        with patch("static_analyzer.LSPClient", side_effect=[MagicMock(), MagicMock()]):
            analyzer.start_clients()

        assert analyzer._clients_started is True
        assert len(analyzer._engine_clients) == 2


class TestStartClientsWorkspaceReadyDispatch:
    """``start_clients`` calls ``wait_for_server_ready`` exactly when the
    adapter opts in via ``wait_for_workspace_ready``.
    """

    def test_adapter_opting_in_triggers_wait(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        rust_adapter = _make_adapter("Rust", wait_for_workspace_ready=True)
        analyzer._engine_configs = [EngineConfig(rust_adapter, tmp_path)]

        client = MagicMock(name="RustClient")
        with patch("static_analyzer.LSPClient", return_value=client):
            analyzer.start_clients()

        client.wait_for_server_ready.assert_called_once()

    def test_adapter_not_opting_in_skips_wait(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        py_adapter = _make_adapter("Python", wait_for_workspace_ready=False)
        analyzer._engine_configs = [EngineConfig(py_adapter, tmp_path)]

        client = MagicMock(name="PythonClient")
        with patch("static_analyzer.LSPClient", return_value=client):
            analyzer.start_clients()

        client.wait_for_server_ready.assert_not_called()

    def test_mixed_adapters_only_waits_on_opting_in_clients(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        rust_adapter = _make_adapter("Rust", wait_for_workspace_ready=True)
        py_adapter = _make_adapter("Python", wait_for_workspace_ready=False)
        analyzer._engine_configs = [EngineConfig(py_adapter, tmp_path), EngineConfig(rust_adapter, tmp_path)]

        py_client = MagicMock(name="PythonClient")
        rust_client = MagicMock(name="RustClient")
        with patch("static_analyzer.LSPClient", side_effect=[py_client, rust_client]):
            analyzer.start_clients()

        py_client.wait_for_server_ready.assert_not_called()
        rust_client.wait_for_server_ready.assert_called_once()


class TestStartClientsFallbackWorkspace:
    def test_a_server_that_could_not_load_the_project_is_restarted_on_the_fallback(
        self, analyzer: StaticAnalyzer, tmp_path: Path
    ) -> None:
        adapter = _make_adapter("Kotlin", language_enum=Language.KOTLIN)
        adapter.discover_source_files.return_value = [tmp_path / "A.kt"]  # type: ignore[attr-defined]
        adapter.fallback_workspace_folders.side_effect = [[tmp_path / "sources"]]  # type: ignore[attr-defined]
        analyzer._engine_configs = [EngineConfig(adapter, tmp_path)]
        first, second = MagicMock(name="first"), MagicMock(name="second")
        sa = analyzer
        with patch("static_analyzer.LSPClient", side_effect=[first, second]) as client_cls:
            sa.start_clients()

        first.shutdown.assert_called_once()
        assert client_cls.call_args_list[0].kwargs["workspace_folders"] is None
        assert client_cls.call_args_list[1].kwargs["workspace_folders"] == [tmp_path / "sources"]
        assert [client for _, client in sa._engine_clients] == [second]

    def test_a_server_is_started_on_the_workspace_its_adapter_names(
        self, analyzer: StaticAnalyzer, tmp_path: Path
    ) -> None:
        adapter = _make_adapter("Kotlin", language_enum=Language.KOTLIN)
        adapter.discover_source_files.return_value = [tmp_path / "A.kt"]  # type: ignore[attr-defined]
        adapter.workspace_folders.return_value = [tmp_path / "sources"]  # type: ignore[attr-defined]
        adapter.fallback_workspace_folders.return_value = [tmp_path / "sources"]  # type: ignore[attr-defined]
        analyzer._engine_configs = [EngineConfig(adapter, tmp_path)]
        only = MagicMock(name="only")
        with patch("static_analyzer.LSPClient", side_effect=[only]) as client_cls:
            analyzer.start_clients()

        assert client_cls.call_args_list[0].kwargs["workspace_folders"] == [tmp_path / "sources"]
        only.shutdown.assert_not_called()
        assert [client for _, client in analyzer._engine_clients] == [only]


class TestStartClientsImportFilters:
    def test_a_server_whose_import_hid_sources_is_restarted_on_the_same_workspace(
        self, analyzer: StaticAnalyzer, tmp_path: Path
    ) -> None:
        """The workspace is in the command, and a fresh one would import the filters straight back."""
        adapter = _make_adapter("Java")
        adapter.discover_source_files.return_value = [tmp_path / "A.java"]  # type: ignore[attr-defined]
        adapter.lift_import_filters.return_value = True  # type: ignore[attr-defined]
        analyzer._engine_configs = [EngineConfig(adapter, tmp_path)]
        first, second = MagicMock(name="first"), MagicMock(name="second")

        with patch("static_analyzer.LSPClient", side_effect=[first, second]) as client_cls:
            analyzer.start_clients()

        first.shutdown.assert_called_once()
        adapter.get_lsp_command.assert_called_once()  # type: ignore[attr-defined]
        commands = [call.kwargs["command"] for call in client_cls.call_args_list]
        assert commands == [["java-lsp"], ["java-lsp"]]
        assert [client for _, client in analyzer._engine_clients] == [second]

    def test_a_server_with_nothing_hidden_is_not_restarted(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        adapter = _make_adapter("Java")
        adapter.discover_source_files.return_value = [tmp_path / "A.java"]  # type: ignore[attr-defined]
        analyzer._engine_configs = [EngineConfig(adapter, tmp_path)]
        only = MagicMock(name="only")

        with patch("static_analyzer.LSPClient", side_effect=[only]):
            analyzer.start_clients()

        only.shutdown.assert_not_called()

    def test_stopping_restores_after_the_servers_are_down(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        """A live server would see the restored file and rebuild its project with the filters back."""
        order = MagicMock(**{"restored.return_value": False})  # a true __exit__ would swallow the error
        adapter = _make_adapter("Java")
        adapter.discover_source_files.return_value = [tmp_path / "A.java"]  # type: ignore[attr-defined]
        adapter.session.return_value.__exit__.side_effect = lambda *_: order.restored()  # type: ignore[attr-defined]
        analyzer._engine_configs = [EngineConfig(adapter, tmp_path)]
        client = MagicMock(name="client")
        client.shutdown.side_effect = lambda: order.stopped()

        with patch("static_analyzer.LSPClient", side_effect=[client]):
            analyzer.start_clients()
        analyzer.stop_clients()

        assert [call[0] for call in order.mock_calls] == ["stopped", "restored"]

    def test_an_interrupted_stop_still_ends_the_sessions(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        """A second Ctrl-C lands in ``shutdown``, which only catches ``Exception``."""
        adapter = _make_adapter("Java")
        adapter.discover_source_files.return_value = [tmp_path / "A.java"]  # type: ignore[attr-defined]
        adapter.session.return_value.__exit__.return_value = False  # type: ignore[attr-defined]
        analyzer._engine_configs = [EngineConfig(adapter, tmp_path)]
        client = MagicMock(name="client")
        client.shutdown.side_effect = KeyboardInterrupt

        with patch("static_analyzer.LSPClient", side_effect=[client]):
            analyzer.start_clients()
        with pytest.raises(KeyboardInterrupt):
            analyzer.stop_clients()

        adapter.session.return_value.__exit__.assert_called_once()  # type: ignore[attr-defined]

    def test_a_failed_start_ends_the_sessions_after_its_servers_are_down(
        self, analyzer: StaticAnalyzer, tmp_path: Path
    ) -> None:
        order = MagicMock(**{"restored.return_value": False})  # a true __exit__ would swallow the error
        python, java = _make_adapter("Python"), _make_adapter("Java")
        for adapter in (python, java):
            adapter.discover_source_files.return_value = [tmp_path / "a"]  # type: ignore[attr-defined]
        java.session.return_value.__exit__.side_effect = lambda *_: order.restored()  # type: ignore[attr-defined]
        analyzer._engine_configs = [EngineConfig(python, tmp_path), EngineConfig(java, tmp_path)]
        started = MagicMock(name="python")
        started.shutdown.side_effect = lambda: order.stopped()
        failing = MagicMock(name="java")
        failing.start.side_effect = StaticAnalysisFatalError("cannot analyse")

        with patch("static_analyzer.LSPClient", side_effect=[started, failing]):
            with pytest.raises(StaticAnalysisFatalError):
                analyzer.start_clients()

        assert [call[0] for call in order.mock_calls] == ["stopped", "restored"]


FILTERED_CLASSPATH = (
    b'<classpath><classpathentry including="**/*Rules.java" kind="src" path="src/main/java">'
    b'<attributes><attribute name="maven.pomderived" value="true"/></attributes>'
    b"</classpathentry></classpath>"
)


class TestStartClientsLeavesTheRepositoryAsItWas:
    """``__exit__`` does not run when ``__enter__`` raises, so a start that fails must undo its own edits."""

    @pytest.fixture
    def modules(self, analyzer: StaticAnalyzer, tmp_path: Path) -> list[Path]:
        modules = [tmp_path / "core", tmp_path / "utils"]
        for module in modules:
            module.mkdir()
            (module / ".classpath").write_bytes(FILTERED_CLASSPATH)
        analyzer._engine_configs = [EngineConfig(JavaAdapter(), tmp_path, source_files=[tmp_path / "A.java"])]
        return modules

    def test_after_a_lift_that_fails_on_the_second_file(self, analyzer: StaticAnalyzer, modules: list[Path]) -> None:
        write_bytes = Path.write_bytes
        writes: list[Path] = []

        def fail_the_second_write(path: Path, data: bytes) -> int:
            writes.append(path)
            if len(writes) == 2:
                raise OSError("No space left on device")
            return write_bytes(path, data)

        with (
            patch.object(JavaAdapter, "get_lsp_command", return_value=["jdtls"]),
            patch.object(Path, "write_bytes", fail_the_second_write),
            patch("static_analyzer.LSPClient"),
            pytest.raises(RuntimeError),
            analyzer,
        ):
            pass

        assert len(writes) == 3  # one file lifted, one failed, the first put back
        assert [(module / ".classpath").read_bytes() for module in modules] == [FILTERED_CLASSPATH] * 2

    def test_after_a_restart_that_fails(self, analyzer: StaticAnalyzer, modules: list[Path]) -> None:
        with (
            patch.object(JavaAdapter, "get_lsp_command", return_value=["jdtls"]),
            patch("static_analyzer.LSPClient", side_effect=[MagicMock(name="first"), RuntimeError("no restart")]),
            pytest.raises(RuntimeError),
            analyzer,
        ):
            pass

        assert [(module / ".classpath").read_bytes() for module in modules] == [FILTERED_CLASSPATH] * 2
