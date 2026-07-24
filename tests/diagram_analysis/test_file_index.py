from pathlib import Path
from unittest.mock import MagicMock

from agents.agent_responses import AnalysisInsights, Component
from agents.content_hash import hash_method_body
from agents.file_index_models import FileMethodGroup, MethodEntry
from diagram_analysis.file_index import build_files_index, refresh_method_spans_from_cfg
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node


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


def _static_analysis_with_nodes(*nodes: Node) -> StaticAnalysisResults:
    cfg = CallGraph(nodes={node.fully_qualified_name: node for node in nodes})
    static_analysis = MagicMock(spec=StaticAnalysisResults)
    static_analysis.get_languages.return_value = ["python"]
    static_analysis.get_cfg.return_value = cfg
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
    static_analysis = _static_analysis_with_nodes(Node("foo", NodeType.FUNCTION, "m.py", 2, 3))

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


def test_build_files_index_skips_ignored_files(tmp_path: Path) -> None:
    # The index is built before _strip_ignored runs, so an ignored file (a test module)
    # must be excluded here or it lingers in the saved files/methods_index.
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_thing.py").write_text("def test_x():\n    return 1\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    analysis = AnalysisInsights(
        description="",
        components=[
            Component(
                name="C",
                description="d",
                key_entities=[],
                component_id="c1",
                file_methods=[
                    FileMethodGroup(
                        file_path="tests/test_thing.py",
                        methods=[
                            MethodEntry(
                                qualified_name="tests.test_thing.test_x", start_line=1, end_line=2, node_type="FUNCTION"
                            )
                        ],
                    ),
                    FileMethodGroup(
                        file_path="app.py",
                        methods=[MethodEntry(qualified_name="app.run", start_line=1, end_line=2, node_type="FUNCTION")],
                    ),
                ],
            )
        ],
        components_relations=[],
    )

    files = build_files_index(analysis, tmp_path)

    assert "tests/test_thing.py" not in files
    assert "app.py" in files
