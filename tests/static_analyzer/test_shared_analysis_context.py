"""Cross-engine resolution uses the same complete declarations on cold and warm runs."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer import EngineConfig, StaticAnalyzer
from static_analyzer.analysis_cache import StaticAnalysisCache
from static_analyzer.config import Language, NodeType
from static_analyzer.engine.adapters.csharp_adapter import CSharpAdapter


def declaration(name: str, kind: int, line: int, column: int, end: int, children: list[dict] = []) -> dict:
    return {
        "name": name,
        "kind": kind,
        "range": {"start": {"line": line, "character": 0}, "end": {"line": end, "character": 100}},
        "selectionRange": {"start": {"line": line, "character": column}},
        "children": children,
    }


def analyzer_for(root: Path, reverse: bool = False) -> tuple[StaticAnalyzer, Path, Path]:
    caller, target = root / "app" / "Caller.cs", root / "lib" / "Target.cs"
    caller.parent.mkdir(exist_ok=True)
    target.parent.mkdir(exist_ok=True)
    caller.write_text("class Caller {\n  void Run() {\n    Target.Next();\n  }\n}\n")
    target.write_text("class Target {\n  public static Action Next;\n}\n")
    symbols = {
        caller: [declaration("Caller", NodeType.CLASS, 0, 6, 4, [declaration("Run()", NodeType.METHOD, 1, 7, 3)])],
        target: [declaration("Target", NodeType.CLASS, 0, 6, 2, [declaration("Next", NodeType.FIELD, 1, 23, 1)])],
    }
    analyzer = StaticAnalyzer.__new__(StaticAnalyzer)
    analyzer.repository_path = root
    analyzer.changed_files = set()
    analyzer.collected_diagnostics = {}
    analyzer.ignore_manager = MagicMock()
    analyzer.ignore_manager.should_ignore.return_value = False
    analyzer._loc_for_adapter = MagicMock(return_value=0)
    analyzer._collect_diagnostics_for = MagicMock()
    analyzer._engine_clients = []
    for path in (target, caller) if reverse else (caller, target):
        client = MagicMock()
        client.document_symbol.side_effect = lambda path, **kwargs: symbols[path] if path.exists() else []
        client.send_definition_batch.side_effect = lambda queries: (
            [
                [{"uri": target.as_uri(), "range": {"start": {"line": 1, "character": 23}}}] if target.exists() else []
                for _ in queries
            ],
            set(),
        )
        client.send_implementation_batch.side_effect = lambda queries: ([[] for _ in queries], set())
        client.type_hierarchy_prepare.return_value = []
        client.get_collected_diagnostics.return_value = {}
        analyzer._engine_clients.append((EngineConfig(CSharpAdapter(), path.parent, [path]), client))
    analyzer._engine_configs = [config for config, _ in analyzer._engine_clients]
    return analyzer, caller, target


def edges(result) -> set[tuple[str, str, tuple]]:
    return {
        (
            edge.src_node.file_path,
            edge.dst_node.file_path,
            tuple((site["line"], site["column"]) for site in edge.call_sites),
        )
        for edge in result.get_cfg(Language.CSHARP).edges
    }


@pytest.mark.parametrize("reverse", [False, True])
def test_cross_solution_field_and_target_only_addition_match_full(tmp_path: Path, reverse: bool) -> None:
    analyzer, caller, target = analyzer_for(tmp_path, reverse)
    with (
        patch("static_analyzer.engine.call_graph_builder.time.sleep"),
        patch("static_analyzer.max_concurrent_engines", return_value=0),
    ):
        full = analyzer._run_full_lsp_pass()
        assert edges(full) == {(str(caller), str(target), ((3, 12),))}
        snapshot = full.results[Language.CSHARP].symbols
        assert snapshot is not None
        assert any(s.kind == NodeType.FIELD for s in snapshot)
        assert full.get_package_dependencies(Language.CSHARP)["Caller"]["imports"] == ["Target"]
        target.unlink()
        analyzer.changed_files = {target}
        deleted = analyzer._update_cached_results(full, "baseline")
        assert not edges(deleted)
        assert str(caller) in deleted.results[Language.CSHARP].unresolved_files
        target.write_text("class Target {\n  public static Action Next;\n}\n")
        restored = analyzer._update_cached_results(deleted, "baseline")
        assert edges(restored) == edges(full)
        assert str(caller) not in restored.results[Language.CSHARP].unresolved_files
        assert restored.get_package_dependencies(Language.CSHARP) == full.get_package_dependencies(Language.CSHARP)
        caller.write_text("class Caller {\n  void Run() {\n\n  }\n}\n")
        analyzer.changed_files = {caller}
        without_call = analyzer._update_cached_results(restored, "baseline")
        rebuilt = analyzer._run_full_lsp_pass()
        assert set(without_call.get_cfg(Language.CSHARP).nodes) == set(rebuilt.get_cfg(Language.CSHARP).nodes)
        assert not edges(without_call)
        assert without_call.get_package_dependencies(Language.CSHARP)["Caller"]["imports"] == []
        assert edges(full) == {(str(caller), str(target), ((3, 12),))}


def test_snapshot_survives_cache_relocation_without_runtime_adapters(tmp_path: Path) -> None:
    analyzer, caller, target = analyzer_for(tmp_path)
    with (
        patch("static_analyzer.engine.call_graph_builder.time.sleep"),
        patch("static_analyzer.max_concurrent_engines", return_value=0),
    ):
        result = analyzer._run_full_lsp_pass()
    result.results[Language.CSHARP].closed_documents = {str(target)}
    result.results[Language.CSHARP].unresolved_files = {str(caller)}
    cache = StaticAnalysisCache(tmp_path / "artifact", tmp_path)
    cache.save(result, "baseline")
    moved = tmp_path / "moved"
    loaded = StaticAnalysisCache(tmp_path / "artifact", moved).load_with_sha()
    assert loaded is not None
    symbols = loaded[0].results[Language.CSHARP].symbols
    assert symbols is not None
    assert {s.file_path for s in symbols} == {moved / "app" / caller.name, moved / "lib" / target.name}
    assert loaded[0].results[Language.CSHARP].closed_documents == {str(moved / "lib" / target.name)}
    assert loaded[0].results[Language.CSHARP].unresolved_files == {str(moved / "app" / caller.name)}
    original_symbols = result.results[Language.CSHARP].symbols
    assert original_symbols is not None
    assert all(s.file_path.is_relative_to(tmp_path) and not s.file_path.is_relative_to(moved) for s in original_symbols)
