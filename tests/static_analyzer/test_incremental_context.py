"""Incremental boundaries use shared declarations and the normal edge pipeline."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from static_analyzer.cfg import CallGraph, EdgeKind, ReferenceEdge
from static_analyzer.engine.adapters.csharp_adapter import CSharpAdapter
from static_analyzer.engine.adapters.python_adapter import PythonAdapter
from static_analyzer.engine.analysis_context import AnalysisContext
from static_analyzer.engine.models import LanguageAnalysisResult, SymbolInfo
from static_analyzer.engine.result_converter import convert_to_codeboarding_format
from static_analyzer.incremental_orchestrator import affected_source_files, update_cfg_for_changed_files
from static_analyzer.node import Node
from static_analyzer.config import NodeType


def test_dispatch_dependencies_include_added_and_removed_descendants(tmp_path: Path) -> None:
    base, child, caller, unresolved = [tmp_path / name for name in ("Base.cs", "Child.cs", "Caller.cs", "Other.cs")]
    base.write_text("class Base {}")
    child.write_text("class Child : Base {}")
    caller.write_text("class Caller {}")
    unresolved.write_text("class Other {}")
    adapter = CSharpAdapter()
    context = AnalysisContext()
    context.symbols_for(adapter).add_symbols([SymbolInfo("Base", "Base", 5, base, 0, 6, 0, 13)])
    graph = CallGraph(language="CSharp")
    for name, path in (("Base", base), ("Child", child), ("Caller", caller)):
        graph.add_node(Node(name, NodeType.CLASS, str(path), 1, 1))
    graph.add_edge("Caller", "Base")
    cached = {"call_graph": graph, "unresolved_files": {str(unresolved)}}
    assert affected_source_files(cached, {child}, adapter, context) == {child, caller, unresolved}
    assert affected_source_files(cached, set(), adapter, context) == set()
    graph.add_reference_edge(ReferenceEdge("Child", "Base", EdgeKind.INHERITS))
    child.unlink()
    assert affected_source_files(cached, {child}, adapter, context) == {caller, unresolved}


def test_reference_strategy_discovers_new_outbound_field_in_other_engine(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    other = tmp_path / "other" / "target.py"
    other.parent.mkdir()
    source.write_text("def run():\n    callback()\n")
    other.write_text("callback = lambda: None\n")
    adapter = PythonAdapter()
    context = AnalysisContext()
    table = context.symbols_for(adapter)
    table.add_symbols([SymbolInfo("run", "source.run", 12, source, 0, 4, 1, 14)])
    cached = convert_to_codeboarding_format(table, LanguageAnalysisResult(source_files=[str(source)]), adapter)
    table.add_symbols([SymbolInfo("callback", "target.callback", 13, other, 0, 0, 0, 23)])
    context.freeze()
    assert "target.callback" not in cached["call_graph"].nodes
    client = MagicMock()
    client.get_collected_diagnostics.return_value = {}
    client.send_references_batch.side_effect = lambda queries, **kwargs: ([[] for _ in queries], set())
    definition = {"uri": other.as_uri(), "range": {"start": {"line": 0, "character": 0}}}
    client.send_definition_batch.side_effect = lambda queries: ([[definition] for _ in queries], set())
    ignore = MagicMock()
    ignore.should_ignore.return_value = False
    with patch("static_analyzer.incremental_orchestrator.CallGraphBuilder.collect_symbols") as collect:
        result = update_cfg_for_changed_files(cached, {source}, adapter, tmp_path, client, ignore, context)
    collect.assert_not_called()
    assert [(edge.get_source(), edge.get_destination()) for edge in result["call_graph"].edges] == [
        ("source.run", "target.callback")
    ]
    assert set(result["source_files"]) == {source, other}
    assert result["symbols"] == table.snapshot()


def test_requery_removes_only_outgoing_edges_and_scopes_files(tmp_path: Path) -> None:
    paths = [tmp_path / name for name in ("caller.py", "target.py", "inbound.py", "ignored.py")]
    for path in paths:
        path.write_text("def f(): pass\n")
    caller, target, inbound, ignored = paths
    adapter = PythonAdapter()
    context = AnalysisContext()
    table = context.symbols_for(adapter)
    table.add_symbols([SymbolInfo(path.stem, path.stem, 12, path, 0, 4, 0, 13) for path in paths])
    context.freeze()
    cached = convert_to_codeboarding_format(table, LanguageAnalysisResult(source_files=list(map(str, paths))), adapter)
    cached["call_graph"].add_edge("caller", "target")
    cached["call_graph"].add_edge("inbound", "caller")
    cached["diagnostics"] = {str(target): ["kept"]}
    context.unresolved_files.add(str(ignored))
    client = MagicMock()
    client.get_collected_diagnostics.return_value = {str(caller): ["fresh"]}
    ignore = MagicMock()
    ignore.should_ignore.side_effect = lambda path: path == ignored
    with patch(
        "static_analyzer.incremental_orchestrator.CallGraphBuilder.build", return_value=LanguageAnalysisResult()
    ) as build:
        result = update_cfg_for_changed_files(
            cached,
            set(),
            adapter,
            tmp_path,
            client,
            ignore,
            context,
            {caller, ignored, tmp_path.parent / "external.py"},
        )
    assert set(build.call_args.args[0]) == {caller, target}
    assert set(result["call_graph"].nodes) == {path.stem for path in paths}
    assert [(edge.get_source(), edge.get_destination()) for edge in result["call_graph"].edges] == [
        ("inbound", "caller")
    ]
    assert result["diagnostics"] == {str(target): ["kept"], str(caller): ["fresh"]}
    assert result["unresolved_files"] == {str(ignored)}
