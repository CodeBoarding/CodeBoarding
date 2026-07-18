from pathlib import Path
from unittest.mock import MagicMock

from agents.agent_responses import AnalysisInsights, Component
from agents.content_hash import hash_method_body
from agents.file_index_models import FileMethodGroup, MethodEntry
from diagram_analysis.file_index import build_files_index, changed_member_qnames, refresh_method_spans_from_cfg
from repo_utils.change_detector import ChangeSet, FileChange
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import NodeType
from static_analyzer.program_graph import ProgramGraph, ProgramNode, ProgramNodeKind
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


STALE_HASH = "deadbeefdeadbeef"


def _method(qname: str, start: int, end: int, content_hash: str) -> MethodEntry:
    return MethodEntry(
        qualified_name=qname, start_line=start, end_line=end, node_type="FUNCTION", content_hash=content_hash
    )


def _analysis(*groups: FileMethodGroup) -> AnalysisInsights:
    return AnalysisInsights(
        description="",
        components=[
            Component(name="C", description="d", key_entities=[], component_id="c1", file_methods=list(groups))
        ],
        components_relations=[],
    )


def test_changed_member_qnames_detects_body_edit_independent_of_fingerprint(tmp_path: Path) -> None:
    # A body-only edit: the persisted hash is stale, the live span rehashes
    # differently. No cluster membership moved, yet the member surfaces.
    (tmp_path / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    analysis = _analysis(FileMethodGroup(file_path="a.py", methods=[_method("a.foo", 1, 2, STALE_HASH)]))
    static_analysis = _static_analysis_with_nodes(make_symbol("a.foo", NodeType.FUNCTION, "a.py", 1, 2))
    changes = ChangeSet(files=[FileChange(status_code="M", file_path="a.py")])

    assert changed_member_qnames([analysis], static_analysis, tmp_path, changes) == {"a.foo"}


def test_changed_member_qnames_skips_unchanged_body(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    live_hash = hash_method_body(["def foo():", "    return 2"], 1, 2)
    analysis = _analysis(FileMethodGroup(file_path="a.py", methods=[_method("a.foo", 1, 2, live_hash)]))
    static_analysis = _static_analysis_with_nodes(make_symbol("a.foo", NodeType.FUNCTION, "a.py", 1, 2))
    changes = ChangeSet(files=[FileChange(status_code="M", file_path="a.py")])

    assert changed_member_qnames([analysis], static_analysis, tmp_path, changes) == set()


def test_changed_member_qnames_restricts_to_changed_files(tmp_path: Path) -> None:
    # Both bodies differ from their stale hash, but only a.py is in the diff, so
    # a drifted hash in the untouched b.py must not surface.
    (tmp_path / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def bar():\n    return 3\n", encoding="utf-8")
    analysis = _analysis(
        FileMethodGroup(file_path="a.py", methods=[_method("a.foo", 1, 2, STALE_HASH)]),
        FileMethodGroup(file_path="b.py", methods=[_method("b.bar", 1, 2, STALE_HASH)]),
    )
    static_analysis = _static_analysis_with_nodes(
        make_symbol("a.foo", NodeType.FUNCTION, "a.py", 1, 2),
        make_symbol("b.bar", NodeType.FUNCTION, "b.py", 1, 2),
    )
    changes = ChangeSet(files=[FileChange(status_code="M", file_path="a.py")])

    assert changed_member_qnames([analysis], static_analysis, tmp_path, changes) == {"a.foo"}


def test_changed_member_qnames_resolves_alias_to_canonical(tmp_path: Path) -> None:
    # The persisted index carries an alias; the graph node (and cluster member) is
    # the canonical id. The changed member must be reported under the canonical id
    # so it joins the cluster it belongs to.
    (tmp_path / "a.py").write_text("class C:\n    def m(self):\n        return 2\n", encoding="utf-8")
    canonical = "a.C.m"
    node = ProgramNode(
        node_id=canonical,
        kind=ProgramNodeKind.SYMBOL,
        language="python",
        name="m",
        file_path="a.py",
        symbol_type=NodeType.METHOD,
        line_start=2,
        line_end=3,
        metadata={"aliases": ["a.alias"]},
    )
    analysis = _analysis(FileMethodGroup(file_path="a.py", methods=[_method("a.alias", 2, 3, STALE_HASH)]))
    static_analysis = _static_analysis_with_nodes(node)
    changes = ChangeSet(files=[FileChange(status_code="M", file_path="a.py")])

    assert changed_member_qnames([analysis], static_analysis, tmp_path, changes) == {canonical}
