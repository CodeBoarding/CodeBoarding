from pathlib import Path
from unittest.mock import MagicMock

from agents.agent_responses import AnalysisInsights, Component, SourceCodeReference
from agents.content_hash import hash_method_body
from agents.file_index_models import FileMethodGroup, MethodEntry
from diagram_analysis.file_index import (
    build_files_index,
    refresh_method_locations_from_cfg,
    refresh_method_spans_from_cfg,
)
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import NodeType
from static_analyzer.program_graph import ProgramGraph, ProgramNode
from tests.program_graph_factory import make_symbol


def _analysis_with_method(file_path: str, qname: str, start: int, end: int) -> AnalysisInsights:
    return AnalysisInsights(
        description="",
        components=[
            Component(
                name="C",
                description="d",
                key_entities=[],
                component_id="c1",
                file_methods=[
                    FileMethodGroup(
                        file_path=file_path,
                        methods=[
                            MethodEntry(qualified_name=qname, start_line=start, end_line=end, node_type="FUNCTION")
                        ],
                    )
                ],
            )
        ],
        components_relations=[],
    )


def _static_analysis_with_nodes(*nodes: ProgramNode) -> StaticAnalysisResults:
    cfg = ProgramGraph(language="python", nodes={node.id: node for node in nodes})
    static_analysis = MagicMock(spec=StaticAnalysisResults)
    static_analysis.get_languages.return_value = ["python"]
    static_analysis.get_program_graph.return_value = cfg
    return static_analysis


def test_build_files_index_hashes_carried_span(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    analysis = _analysis_with_method("m.py", "foo", start=1, end=2)

    files = build_files_index(analysis, tmp_path)

    method = files["m.py"].methods[0]
    assert method.content_hash == hash_method_body(["def foo():", "    return 1"], 1, 2)
    assert method.content_hash != ""


def test_refresh_spans_then_index_reflects_live_cfg_span(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("# added line\ndef foo():\n    return 1\n", encoding="utf-8")
    analysis = _analysis_with_method("m.py", "foo", start=1, end=2)
    static_analysis = _static_analysis_with_nodes(make_symbol("foo", NodeType.FUNCTION, "m.py", 2, 3))

    refresh_method_spans_from_cfg(analysis, static_analysis, tmp_path)
    files = build_files_index(analysis, tmp_path)

    method = files["m.py"].methods[0]
    assert method.content_hash == hash_method_body(["# added line", "def foo():", "    return 1"], 2, 3)
    assert method.content_hash != ""


def test_refresh_spans_empty_hash_when_method_absent_from_live_cfg(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def something_else():\n    return 42\n", encoding="utf-8")
    analysis = _analysis_with_method("m.py", "foo", start=1, end=2)

    refresh_method_spans_from_cfg(analysis, _static_analysis_with_nodes(), tmp_path)
    files = build_files_index(analysis, tmp_path)

    assert files["m.py"].methods[0].content_hash == ""


def test_refresh_locations_moves_stable_symbol_without_rewriting_component(tmp_path: Path) -> None:
    analysis = _analysis_with_method("old.py", "pkg.foo", start=1, end=2)
    analysis.components[0].key_entities = [
        SourceCodeReference(
            qualified_name="pkg.foo",
            reference_file="old.py",
            reference_start_line=1,
            reference_end_line=2,
        )
    ]
    static_analysis = _static_analysis_with_nodes(
        make_symbol("pkg.foo", NodeType.FUNCTION, "new.py", 7, 11),
    )

    refresh_method_locations_from_cfg(analysis, static_analysis, tmp_path)

    component = analysis.components[0]
    assert component.file_methods[0].file_path == "new.py"
    assert (component.file_methods[0].methods[0].start_line, component.file_methods[0].methods[0].end_line) == (
        7,
        11,
    )
    assert component.key_entities[0].reference_file == "new.py"
    assert component.key_entities[0].reference_start_line == 7
    assert component.key_entities[0].reference_end_line == 11
