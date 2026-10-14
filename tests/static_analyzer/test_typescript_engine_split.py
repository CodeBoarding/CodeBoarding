"""A large TypeScript monorepo's full pass runs one server per tsconfig project, and the merge links across them.

Why: one tsserver holding every project's program runs out of memory on a large monorepo and answers nothing.
"""

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from infra.repo_utils.ignore import RepoIgnoreManager
from static_analyzer import TYPESCRIPT_SPLIT_MIN_FILES, EngineConfig, StaticAnalyzer, _create_engine_configs
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph, EdgeKind
from static_analyzer.config import Language, NodeType
from static_analyzer.engine.adapters.python_adapter import PythonAdapter
from static_analyzer.engine.adapters.typescript_adapter import TypeScriptAdapter
from static_analyzer.node import Node
from static_analyzer.typescript_config_scanner import TypeScriptConfigScanner, TypeScriptProject
from tests.static_analyzer.test_engine_configs import lang

GB = 1024**3


class TestSplit:
    def test_a_large_monorepo_splits_per_project_largest_first_then_the_unclaimed_files(self, tmp_path: Path):
        small = _project(tmp_path / "packages" / "small", 100, "s")
        large = _project(tmp_path / "packages" / "large", TYPESCRIPT_SPLIT_MIN_FILES, "l")
        stray = [tmp_path / "scripts" / "build.js"]
        (config,) = _configs(tmp_path, [small, large], stray)

        assert config.source_files == small.files + large.files + stray
        assert [part.source_files for part in config.split] == [large.files, small.files, stray]
        assert all(part.project_path == tmp_path and not part.split for part in config.split)

    @pytest.mark.parametrize("projects", [[("a", 10), ("b", 10)], [("a", TYPESCRIPT_SPLIT_MIN_FILES * 2)]])
    def test_a_small_monorepo_or_a_single_project_is_not_split(self, tmp_path: Path, projects: list):
        (config,) = _configs(tmp_path, [_project(tmp_path / name, count, name) for name, count in projects], [])
        assert config.split == []


class TestFullPass:
    def test_a_one_shot_run_starts_each_part_in_turn_and_merges_them_in_order(self, tmp_path: Path):
        analyzer, whole = _analyzer(tmp_path, slots=1)
        servers = {id(part): MagicMock(name=f"part {index}") for index, part in enumerate(whole.split)}

        absorb = _run(analyzer, MagicMock(side_effect=lambda config: servers[id(config)]))

        analysed = cast(MagicMock, analyzer._run_full_analysis)
        assert [call.args[1] for call in analysed.call_args_list] == list(servers.values())
        for server in servers.values():
            server.shutdown.assert_called_once()
        merged = [analysis["source_files"] for _adapter, analysis in absorb.call_args.args[1]]
        assert merged == [part.source_files for part in whole.split]

    def test_a_part_that_fails_leaves_the_whole_config_out(self, tmp_path: Path):
        """A merge of the other parts would cache the config as complete without one of its projects."""
        analyzer, whole = _analyzer(tmp_path, slots=1)
        analysed = cast(MagicMock, analyzer._run_full_analysis)
        analysed.side_effect = [{"source_files": whole.split[0].source_files}, RuntimeError("tsserver crashed")]

        absorb = _run(analyzer, MagicMock())

        assert absorb.call_args.args[1] == []

    def test_the_editor_stops_its_server_while_the_parts_run_and_starts_a_fresh_one_after(self, tmp_path: Path):
        """The editor's server holds every program it opened; beside a part, that is two projects' memory."""
        analyzer, whole = _analyzer(tmp_path, slots=0)
        resident = MagicMock(name="resident")
        analyzer._engine_clients = [(whole, resident)]
        events: list[str] = []
        resident.shutdown.side_effect = lambda: events.append("stop resident")
        fresh = MagicMock(name="fresh")

        def spawn(config: EngineConfig) -> MagicMock:
            if config is whole:
                events.append("start resident")
                return fresh
            name = config.source_files[0].stem
            events.append(f"start {name}")
            return MagicMock(**{"shutdown.side_effect": lambda: events.append(f"stop {name}")})

        _run(analyzer, MagicMock(side_effect=spawn))

        # The second stop is the replacement's own, a no-op on a stopped server.
        expected = ["stop resident", "start a", "stop a", "start b", "stop b", "stop resident", "start resident"]
        assert events == expected
        assert analyzer._engine_clients == [(whole, fresh)]


@pytest.mark.parametrize("changed", [None, set()])
def test_a_warm_start_re_analyses_fully_as_the_parts_and_updates_changed_files_on_the_config(
    tmp_path: Path, changed: set | None
):
    analyzer, whole = _analyzer(tmp_path, slots=1)
    analyzer.ignore_manager = RepoIgnoreManager(tmp_path)
    spawn = MagicMock()

    with (
        patch.object(analyzer, "_spawn_engine_client", spawn),
        patch.object(analyzer, "_changed_files_for_language", return_value=changed),
        patch.object(analyzer, "_extract_language_dict", return_value={}),
        patch("static_analyzer.update_cfg_for_changed_files", return_value={}),
        patch.object(analyzer, "_absorb_and_link"),
        patch("static_analyzer.track_lsp_result"),
    ):
        analyzer._update_cached_results(MagicMock(), "baseline")

    started = [call.args[0] for call in spawn.call_args_list]
    assert started == (whole.split if changed is None else [whole])


@pytest.mark.parametrize("slots, share", [(0, 8 * GB), (1, 8 * GB), (2, 4 * GB)])
def test_tsserver_heap_is_its_share_of_the_budget_less_what_node_holds_outside_it(slots: int, share: int):
    analyzer = StaticAnalyzer.__new__(StaticAnalyzer)
    analyzer._server_slots = slots
    analyzer.ignore_manager = MagicMock()
    analyzer._request_timeout_override = None
    options = {}
    for adapter in (TypeScriptAdapter(), PythonAdapter()):
        with (
            patch("static_analyzer.engine.lsp_recycler.default_memory_budget", return_value=8 * GB),
            patch("static_analyzer.LSPClient") as client,
        ):
            analyzer._start_engine_client(EngineConfig(adapter, Path("/repo")))
        options[adapter.language] = client.call_args.kwargs["init_options"]

    assert options["TypeScript"]["maxTsServerMemory"] == share // 1024**2 - 512
    assert "maxTsServerMemory" not in options["Python"]


def test_a_base_in_another_project_is_linked_at_the_merge():
    """Each part's engine links bases among its own classes; the class it extends lies in another part."""
    core = _hierarchy_analysis({"core.Base": "/r/core/base.ts"}, {})
    app = _hierarchy_analysis({"app.Widget": "/r/app/widget.ts"}, {"app.Widget": ["Base"]})
    adapter = MagicMock(results_language=Language.TYPESCRIPT)
    results = StaticAnalysisResults()

    StaticAnalyzer.__new__(StaticAnalyzer)._absorb_and_link(results, [(adapter, core), (adapter, app)])

    hierarchy = results.get_hierarchy(Language.TYPESCRIPT)
    assert hierarchy["app.Widget"]["superclasses"] == ["core.Base"]
    assert hierarchy["core.Base"]["subclasses"] == ["app.Widget"]
    inherits = results.get_cfg(Language.TYPESCRIPT).reference_edges
    assert [(ref.src, ref.dst) for ref in inherits if ref.kind == EdgeKind.INHERITS] == [("app.Widget", "core.Base")]


def _project(root: Path, count: int, prefix: str) -> TypeScriptProject:
    return TypeScriptProject(root=root, files=[root / f"{prefix}_{i}.ts" for i in range(count)])


def _configs(tmp_path: Path, projects: list[TypeScriptProject], unclaimed: list[Path]) -> list[EngineConfig]:
    with (
        patch.object(TypeScriptConfigScanner, "find_typescript_projects", return_value=projects),
        patch.object(TypeScriptConfigScanner, "find_unclaimed_family_files", return_value=unclaimed),
    ):
        return _create_engine_configs([lang("TypeScript")], tmp_path, RepoIgnoreManager(tmp_path))


def _analyzer(tmp_path: Path, slots: int) -> tuple[StaticAnalyzer, EngineConfig]:
    adapter = TypeScriptAdapter()
    parts = [EngineConfig(adapter, tmp_path, [tmp_path / name / f"{name}.ts"]) for name in ("a", "b")]
    whole = EngineConfig(adapter, tmp_path, [path for part in parts for path in part.source_files], split=parts)
    analyzer = StaticAnalyzer.__new__(StaticAnalyzer)
    analyzer.repository_path = tmp_path
    analyzer._server_slots = slots
    analyzer.collected_diagnostics = {}
    analyzer._engine_configs = [whole]
    analyzer._engine_clients = []
    analyzer._loc_for_adapter = MagicMock(return_value=0)  # type: ignore[method-assign]
    analyzer._collect_diagnostics_for = MagicMock()  # type: ignore[method-assign]
    analyzer._run_full_analysis = MagicMock(  # type: ignore[method-assign]
        side_effect=lambda config, _client: {"source_files": config.source_files}
    )
    return analyzer, whole


def _run(analyzer: StaticAnalyzer, spawn: MagicMock) -> MagicMock:
    with (
        patch.object(analyzer, "_spawn_engine_client", spawn),
        patch.object(analyzer, "_absorb_and_link") as absorb,
        patch("static_analyzer.track_lsp_result"),
    ):
        analyzer._run_full_lsp_pass()
    return absorb


def _hierarchy_analysis(classes: dict[str, str], bases: dict[str, list[str]]) -> dict:
    cfg = CallGraph(language="typescript")
    for qname, path in classes.items():
        cfg.add_node(Node(qname, NodeType.CLASS, path, 1, 2))
    hierarchy = {
        qname: {"superclasses": [], "subclasses": [], "declared_bases": bases.get(qname, [])} for qname in classes
    }
    return {"call_graph": cfg, "class_hierarchies": hierarchy, "source_files": sorted(set(classes.values()))}
