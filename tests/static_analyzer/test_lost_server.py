"""A language server that loses its backend must fail the run, not cache a hollow graph.

Why: typescript-language-server outlives a crashed tsserver and answers every later request empty.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer import EngineConfig, StaticAnalyzer
from static_analyzer.config import Language
from static_analyzer.engine.hierarchy_builder import HierarchyBuilder
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.models import SymbolInfo
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.exceptions import LanguageServerStalledError, StaticAnalysisFatalError

# Verbatim from typescript-language-server 4.3.4 after its tsserver child was killed with SIGABRT.
TSSERVER_EXITED = "[lspserver] [tsclient] [tsserver] Exited. Code: null. Signal: SIGABRT"


def _running_client() -> LSPClient:
    client = LSPClient(["fake-server"], Path("/project"), default_timeout=1)
    client._process = MagicMock()
    client._process.poll.return_value = None
    client._write_message = MagicMock()  # type: ignore[method-assign]
    return client


def _lost_backend(client: LSPClient) -> None:
    client._handle_notification("window/logMessage", {"type": 1, "message": TSSERVER_EXITED})


class TestClient:
    def test_every_request_after_the_backend_exited_fails_without_reaching_the_server(self):
        client = _running_client()
        _lost_backend(client)
        assert not client.alive
        with pytest.raises(LanguageServerStalledError, match="Signal: SIGABRT"):
            client.document_symbol(Path("/project/a.ts"))
        with pytest.raises(LanguageServerStalledError):
            client.send_definition_batch([(Path("/project/a.ts"), 0, 0)])
        with pytest.raises(LanguageServerStalledError):
            client.check_alive()
        client._write_message.assert_not_called()  # type: ignore[attr-defined]

    def test_other_log_messages_leave_the_server_alive(self):
        client = _running_client()
        client._handle_notification("window/logMessage", {"type": 3, "message": "[tsserver] Starting tsserver"})
        assert client.alive
        client.check_alive()

    def test_a_restart_forgets_the_lost_backend(self):
        client = _running_client()
        _lost_backend(client)
        with patch.object(client, "shutdown"), patch.object(client, "start"):
            client.restart()
        assert client.alive

    def test_an_exited_process_fails_the_check(self):
        client = _running_client()
        client._process.poll.return_value = 137  # type: ignore[union-attr]
        assert not client.alive
        with pytest.raises(StaticAnalysisFatalError, match="exited"):
            client.check_alive()

    def test_a_failure_the_hierarchy_builder_swallows_still_fails_the_final_check(self):
        """The builder tolerates a class it cannot ask about; the lost backend must outlive that."""
        client = _running_client()
        client._server_capabilities = {"typeHierarchyProvider": True}
        _lost_backend(client)
        adapter = MagicMock()
        adapter.is_class_like.return_value = True
        adapter.resolves_bases_by_definition = False
        symbol = SymbolInfo("A", "a.A", 5, Path("/project/a.ts"), 0, 0, 3, 0)
        symbols = MagicMock(primary_file_symbols={"/project/a.ts": [symbol]}, symbols={"a.A": symbol})
        HierarchyBuilder(client, symbols, SourceInspector(), adapter).build()
        with pytest.raises(LanguageServerStalledError):
            client.check_alive()


def _analyzer(tmp_path: Path, client: MagicMock, owns_documents: bool = False) -> tuple[StaticAnalyzer, EngineConfig]:
    source = tmp_path / "a.ts"
    source.write_text("export function a() { return b(); }\n")
    analyzer = StaticAnalyzer.__new__(StaticAnalyzer)
    analyzer.repository_path = tmp_path
    analyzer.changed_files = {source}
    analyzer.ignore_manager = MagicMock()
    analyzer.collected_diagnostics = {}
    analyzer._server_slots = 0
    adapter = MagicMock(file_extensions={".ts"}, results_language=Language.TYPESCRIPT, fail_on_empty_symbols=True)
    # Explicit: a MagicMock attribute is truthy, which would read as a server that owns its documents.
    adapter.workspace_owns_documents = owns_documents
    config = EngineConfig(adapter, tmp_path, [source])
    analyzer._engine_configs = [config]
    analyzer._engine_clients = [(config, client)]
    return analyzer, config


class TestAnalyzer:
    def test_a_full_analysis_whose_server_was_lost_fails_as_lost_not_as_empty(self, tmp_path: Path):
        client = MagicMock()
        client.check_alive.side_effect = LanguageServerStalledError("lost")
        analyzer, config = _analyzer(tmp_path, client)
        with patch("static_analyzer.CallGraphBuilder") as builder:
            builder.return_value.symbol_table.symbols = {}
            with pytest.raises(LanguageServerStalledError):
                analyzer._run_full_analysis(config, client)

    @pytest.mark.parametrize("pass_name", ["full", "warm-start"])
    def test_a_server_that_died_in_an_earlier_run_is_replaced_first(self, tmp_path: Path, pass_name: str):
        """An editor keeps its analyzer; without the replacement every later run would fail too."""
        dead = MagicMock(alive=False)
        fresh = MagicMock(alive=True)
        analyzer, config = _analyzer(tmp_path, dead)
        analyzer._spawn_engine_client = MagicMock(return_value=fresh)  # type: ignore[method-assign]
        with (
            patch.object(analyzer, "_run_full_analysis", return_value={}) as full,
            patch("static_analyzer.update_cfg_for_changed_files", return_value={}) as update,
            patch.object(analyzer, "_extract_language_dict", return_value={}),
            patch.object(analyzer, "_collect_diagnostics_for"),
            patch.object(analyzer, "_loc_for_adapter", return_value=0),
            patch.object(analyzer, "_absorb_and_link"),
            patch("static_analyzer.track_lsp_result"),
            patch.dict("os.environ", {}, clear=True),
        ):
            if pass_name == "full":
                analyzer._run_full_lsp_pass()
                assert full.call_args.args[1] is fresh
            else:
                analyzer._update_cached_results(MagicMock(), "baseline")
                assert update.call_args.args[5] is fresh
                fresh.check_alive.assert_called_once()
        dead.shutdown.assert_called_once()
        assert analyzer._engine_clients == [(config, fresh)]

    def test_a_warm_start_whose_server_was_lost_during_the_update_fails(self, tmp_path: Path):
        client = MagicMock(alive=True)
        client.check_alive.side_effect = LanguageServerStalledError("lost")
        analyzer, _config = _analyzer(tmp_path, client)
        with (
            patch("static_analyzer.update_cfg_for_changed_files", return_value={}),
            patch.object(analyzer, "_extract_language_dict", return_value={}),
        ):
            with pytest.raises(LanguageServerStalledError):
                analyzer._update_cached_results(MagicMock(), "baseline")

    def test_a_symbol_lookup_on_a_lost_server_fails_instead_of_reading_as_no_symbols(self, tmp_path: Path):
        client = MagicMock()
        client.document_symbol.side_effect = LanguageServerStalledError("lost")
        analyzer, _config = _analyzer(tmp_path, client)
        with pytest.raises(LanguageServerStalledError):
            analyzer.get_file_symbols(tmp_path / "a.ts")

    @pytest.mark.parametrize("owns_documents", [False, True], ids=["opened-documents", "workspace-documents"])
    def test_a_lookup_opens_its_file_unless_the_server_reads_documents_from_the_project(
        self, tmp_path: Path, owns_documents: bool
    ):
        """A replaced server starts with nothing open; C# would add a second copy of a linked file it opened."""
        client = MagicMock()
        client.document_symbol.return_value = [{"name": "a"}]
        client.send_definition_batch.return_value = [[{"uri": (tmp_path / "b.ts").as_uri()}]]
        analyzer, _config = _analyzer(tmp_path, client, owns_documents)
        assert analyzer.get_file_symbols(tmp_path / "a.ts") == [{"name": "a"}]
        with patch("static_analyzer.SourceInspector") as inspector:
            inspector.return_value.find_call_sites.return_value = [MagicMock(lsp_line=0, lsp_column=22)]
            assert analyzer.discover_file_dependencies(tmp_path / "a.ts") == [str(tmp_path / "b.ts")]
        assert client.did_open.call_count == (0 if owns_documents else 2)
