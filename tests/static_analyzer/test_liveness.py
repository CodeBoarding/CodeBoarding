"""A language server that keeps running but stops answering must fail the run, not shrink the graph.

The case behind it: typescript-language-server keeps replying after the tsserver behind it runs out of
memory, and every reply is an empty answer. On kilocode that cached 0 TypeScript call edges for 345,995
call sites and reported success.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer import EngineConfig, StaticAnalyzer
from static_analyzer.config import Language, NodeType
from static_analyzer.engine.call_graph_builder import CallGraphBuilder
from static_analyzer.engine.edge_builder import build_edges_via_definitions
from static_analyzer.engine.liveness import (
    CHECK_EVERY,
    MIN_SILENT_CALL_SITES,
    MIN_SILENT_FILES,
    Canary,
    LanguageServerStalledError,
)
from static_analyzer.exceptions import StaticAnalysisFatalError
from tests.static_analyzer.test_call_graph_builder import ONE_SYMBOL, _make_adapter
from tests.static_analyzer.test_call_graph_builder import _make_lsp as _make_symbol_lsp
from tests.static_analyzer.test_edge_builder import _make_ctx, _make_lsp, _sym


class TestCanary:
    def test_re_asks_every_interval_and_raises_once_the_answer_is_gone(self):
        answers = {"alive": True}
        canary: Canary[str] = Canary("Phase X", lambda _q: answers["alive"])
        canary.answered("first")
        for _ in range(CHECK_EVERY):
            canary.tick()
        answers["alive"] = False
        for _ in range(CHECK_EVERY - 1):
            canary.tick()
        with pytest.raises(LanguageServerStalledError, match="stopped answering after 200 files"):
            canary.tick()

    def test_keeps_the_first_question_it_heard(self):
        asked: list[str] = []

        def ask(question: str) -> bool:
            asked.append(question)
            return True

        canary: Canary[str] = Canary("Phase X", ask)
        canary.answered("first")
        canary.answered("second")
        canary.check()
        assert asked == ["first"]

    def test_nothing_heard_yet_is_not_a_stall(self):
        canary: Canary[str] = Canary("Phase X", lambda _q: False)
        for _ in range(CHECK_EVERY * 2):
            canary.tick()
        canary.finish()

    def test_a_one_file_phase_is_not_asked_again_at_the_end(self):
        """The one answer it has is the one the phase just got."""
        ask = MagicMock(return_value=False)
        canary: Canary[str] = Canary("Phase X", ask)
        canary.answered("first")
        canary.tick()
        canary.finish()
        ask.assert_not_called()

    def test_a_short_phase_is_asked_again_at_the_end(self):
        canary: Canary[str] = Canary("Phase X", lambda _q: False)
        canary.answered("first")
        canary.tick()
        canary.tick()
        with pytest.raises(LanguageServerStalledError, match="after 2 files"):
            canary.finish()

    def test_a_phase_ending_on_a_re_ask_is_not_asked_twice(self):
        ask = MagicMock(return_value=True)
        canary: Canary[str] = Canary("Phase X", ask)
        canary.answered("first")
        for _ in range(CHECK_EVERY):
            canary.tick()
        canary.finish()
        ask.assert_called_once_with("first")

    def test_a_re_ask_that_times_out_is_a_stall(self):
        canary: Canary[str] = Canary("Phase X", MagicMock(side_effect=TimeoutError))
        canary.answered("first")
        with pytest.raises(LanguageServerStalledError):
            canary.check()

    def test_an_answer_with_nothing_to_ask_again_still_counts_as_heard(self):
        """A C# linked file's symbols come from a workspace query, not from a question about that file."""
        ask = MagicMock(return_value=False)
        canary: Canary[str] = Canary("Phase X", ask)
        canary.answered()
        canary.require_heard(MIN_SILENT_FILES, MIN_SILENT_FILES, "requests")
        canary.tick()
        canary.tick()
        canary.finish()
        ask.assert_not_called()

    def test_a_long_phase_is_asked_again_at_the_end(self):
        canary: Canary[str] = Canary("Phase X", lambda _q: False)
        canary.answered("first")
        with pytest.raises(LanguageServerStalledError):
            for _ in range(CHECK_EVERY + 1):
                canary.tick()
            canary.finish()

    def test_silence_counts_only_above_the_threshold(self):
        canary: Canary[str] = Canary("Phase X", lambda _q: True)
        canary.require_heard(9, 10, "requests")
        with pytest.raises(LanguageServerStalledError, match="answered none of 10 requests"):
            canary.require_heard(10, 10, "requests")

    def test_a_stall_is_fatal(self):
        assert issubclass(LanguageServerStalledError, StaticAnalysisFatalError)


def _calling_files(tmp_path: Path, count: int, calls_per_file: int = 1) -> tuple[list[Path], Path]:
    """*count* files that each call ``helper``, declared in one more file."""
    target = tmp_path / "lib.py"
    target.write_text("def helper():\n    pass\n")
    files = []
    for i in range(count):
        path = tmp_path / f"app_{i}.py"
        path.write_text("def main():\n" + "    helper()\n" * calls_per_file)
        files.append(path)
    return files, target


def _definitions_ctx(files: list[Path], target: Path):
    lsp = _make_lsp()
    ctx, adapter = _make_ctx(lsp)
    st = ctx.symbol_table
    callee = _sym("helper", "lib.helper", NodeType.FUNCTION, str(target), 0, 4, 1)
    st._symbols[callee.qualified_name] = callee
    st._file_symbols[str(target)] = [callee]
    st._primary_file_symbols[str(target)] = [callee]
    for i, path in enumerate(files):
        caller = _sym("main", f"app_{i}.main", NodeType.FUNCTION, str(path), 0, 4, 10_000)
        st._symbols[caller.qualified_name] = caller
        st._file_symbols[str(path)] = [caller]
        st._primary_file_symbols[str(path)] = [caller]
    st.build_indices()
    return lsp, ctx, adapter


class TestDefinitionsPhase:
    def test_a_server_that_stops_answering_mid_run_is_caught(self, tmp_path: Path):
        files, target = _calling_files(tmp_path, CHECK_EVERY + 20)
        lsp, ctx, adapter = _definitions_ctx(files, target)
        answered = {"batches": 0}

        def batch(queries: list) -> list:
            answered["batches"] += 1
            alive = answered["batches"] <= 30
            return [
                [{"uri": target.as_uri(), "range": {"start": {"line": 0, "character": 4}}}] if alive else []
                for _ in queries
            ]

        lsp.send_definition_batch.side_effect = batch
        with pytest.raises(LanguageServerStalledError, match="Phase 2 \\(definitions\\)"):
            build_edges_via_definitions(adapter, ctx, files)

    def test_a_server_that_never_answers_is_caught(self, tmp_path: Path):
        files, target = _calling_files(tmp_path, 1, calls_per_file=MIN_SILENT_CALL_SITES)
        lsp, ctx, adapter = _definitions_ctx(files, target)
        with pytest.raises(LanguageServerStalledError, match="answered none of"):
            build_edges_via_definitions(adapter, ctx, files)

    def test_a_short_run_without_answers_is_left_alone(self, tmp_path: Path):
        files, target = _calling_files(tmp_path, 3)
        lsp, ctx, adapter = _definitions_ctx(files, target)
        assert build_edges_via_definitions(adapter, ctx, files) == {}

    def test_an_answer_through_the_receiver_counts_as_heard(self, tmp_path: Path):
        """A member typed as something external answers nothing, its receiver still does (mermaid's ``log``)."""
        files, target = _calling_files(tmp_path, 1, calls_per_file=MIN_SILENT_CALL_SITES)
        lsp, ctx, adapter = _definitions_ctx(files, target)
        answer = [{"uri": target.as_uri(), "range": {"start": {"line": 0, "character": 4}}}]
        lsp.send_definition_batch.side_effect = lambda queries: [[] if len(queries) > 1 else answer for _ in queries]

        def receivers(_path: Path, unresolved: list) -> dict:
            site = unresolved[0]
            return {(site.lsp_line, site.lsp_column): (site, "member")}

        with patch.object(ctx.source_inspector, "receivers_of", side_effect=receivers):
            build_edges_via_definitions(adapter, ctx, files)
        assert ctx.definitions.heard

    def test_a_live_server_passes_every_check(self, tmp_path: Path):
        files, target = _calling_files(tmp_path, CHECK_EVERY * 2 + 5)
        lsp, ctx, adapter = _definitions_ctx(files, target)
        lsp.send_definition_batch.side_effect = lambda queries: [
            [{"uri": target.as_uri(), "range": {"start": {"line": 0, "character": 4}}}] for _ in queries
        ]
        edges = build_edges_via_definitions(adapter, ctx, files)
        assert ("app_0.main", "lib.helper") in edges


class TestSymbolsPhase:
    def _builder(self, lsp: MagicMock) -> CallGraphBuilder:
        return CallGraphBuilder(lsp, _make_adapter(), Path("/project"), Path("/project"))

    def test_a_server_that_stops_answering_mid_run_is_caught(self):
        lsp = _make_symbol_lsp()
        calls = {"n": 0}

        def symbols(_path, timeout=None):
            calls["n"] += 1
            return ONE_SYMBOL if calls["n"] <= 30 else []

        lsp.document_symbol.side_effect = symbols
        files = [Path(f"/project/file_{i}.py") for i in range(CHECK_EVERY + 10)]
        with pytest.raises(LanguageServerStalledError, match="Phase 1 \\(symbols\\)"):
            self._builder(lsp)._discover_symbols(files)

    def test_a_server_that_never_answers_is_caught(self):
        lsp = _make_symbol_lsp()
        files = [Path(f"/project/file_{i}.py") for i in range(MIN_SILENT_FILES)]
        with pytest.raises(LanguageServerStalledError, match="answered none of 50 document symbol requests"):
            self._builder(lsp)._discover_symbols(files)

    def test_a_few_empty_files_are_left_alone(self):
        lsp = _make_symbol_lsp()
        files = [Path(f"/project/file_{i}.py") for i in range(MIN_SILENT_FILES - 1)]
        self._builder(lsp)._discover_symbols(files)


class TestAfterTheLastPhase:
    def test_the_server_is_asked_once_more_after_the_hierarchy(self):
        """Implementations and the hierarchy read an empty answer as "no subtypes", so they come after a re-ask."""
        lsp = _make_symbol_lsp()
        lsp.document_symbol.return_value = ONE_SYMBOL
        lsp.send_definition_batch.return_value = [[]]
        builder = CallGraphBuilder(lsp, _make_adapter(), Path("/project"), Path("/project"))

        def edges(_adapter, ctx, _files) -> dict:
            ctx.definitions.answered((Path("/project/a.py"), 0, 0))
            return {}

        with (
            patch("static_analyzer.engine.call_graph_builder.build_edges_via_definitions", side_effect=edges),
            patch("static_analyzer.engine.call_graph_builder.HierarchyBuilder") as hierarchy,
        ):
            hierarchy.return_value.build.return_value = {}
            with pytest.raises(LanguageServerStalledError, match="Phase 2 \\(definitions\\)"):
                builder.build([Path("/project/a.py")])


class TestRestartOnce:
    def _analyzer(self, tmp_path: Path) -> tuple[StaticAnalyzer, EngineConfig, MagicMock, MagicMock]:
        analyzer = StaticAnalyzer.__new__(StaticAnalyzer)
        analyzer.repository_path = tmp_path
        analyzer.collected_diagnostics = {}
        adapter = MagicMock()
        adapter.language = "TypeScript"
        adapter.language_enum = Language.TYPESCRIPT
        adapter.results_language = Language.TYPESCRIPT
        config = EngineConfig(adapter, tmp_path, source_files=[tmp_path / "a.ts"])
        stalled_client = MagicMock(name="stalled")
        analyzer._engine_configs = [config]
        analyzer._engine_clients = [(config, stalled_client)]
        analyzer._loc_for_adapter = MagicMock(return_value=0)
        absorbed = MagicMock()
        analyzer._absorb_into_results = absorbed  # type: ignore[method-assign]
        analyzer._collect_diagnostics_for = MagicMock()  # type: ignore[method-assign]
        return analyzer, config, stalled_client, absorbed

    def test_a_stall_restarts_the_server_and_runs_the_engine_again(self, tmp_path: Path):
        analyzer, config, stalled_client, absorbed = self._analyzer(tmp_path)
        fresh = MagicMock(name="fresh")
        analyzer._spawn_engine_client = MagicMock(return_value=fresh)
        analysis = {"call_graph": MagicMock(), "source_files": []}
        used: list[MagicMock] = []

        def run(_config, client):
            used.append(client)
            if client is stalled_client:
                raise LanguageServerStalledError("stopped answering")
            return analysis

        analyzer._run_full_analysis = MagicMock(side_effect=run)
        with patch.dict("os.environ", {}, clear=True):
            analyzer._run_full_lsp_pass()

        assert used == [stalled_client, fresh]
        stalled_client.shutdown.assert_called_once()
        assert analyzer._engine_clients == [(config, fresh)]
        absorbed.assert_called_once()

    def test_a_second_stall_fails_the_run(self, tmp_path: Path):
        analyzer, _config, _stalled, absorbed = self._analyzer(tmp_path)
        analyzer._spawn_engine_client = MagicMock(return_value=MagicMock(name="fresh"))
        analyzer._run_full_analysis = MagicMock(side_effect=LanguageServerStalledError("stopped answering"))
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(LanguageServerStalledError, match="after a restart too"):
                analyzer._run_full_lsp_pass()
        absorbed.assert_not_called()

    def test_a_server_that_cannot_restart_fails_the_run(self, tmp_path: Path):
        analyzer, _config, _stalled, absorbed = self._analyzer(tmp_path)
        analyzer._spawn_engine_client = MagicMock(side_effect=RuntimeError("tsserver missing"))
        analyzer._run_full_analysis = MagicMock(side_effect=LanguageServerStalledError("stopped answering"))
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(StaticAnalysisFatalError, match="Could not restart the TypeScript server"):
                analyzer._run_full_lsp_pass()
