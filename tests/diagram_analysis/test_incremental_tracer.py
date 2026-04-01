"""Tests for the incremental tracer and scope classification."""

from unittest.mock import MagicMock

from agents.change_status import ChangeStatus
from diagram_analysis.checkpoints import FileComponentIndex
from diagram_analysis.incremental_models import TraceResult, TraceStopReason
from diagram_analysis.incremental_tracer import (
    ChangeGroup,
    ChangedMethodContext,
    MethodResolver,
    _build_change_groups,
    _build_initial_prompt,
    _get_neighbors,
    classify_scope,
)
from diagram_analysis.incremental_types import FileDelta, IncrementalDelta, MethodChange
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node
from static_analyzer.constants import NodeType


def _make_node(qname: str, file_path: str, start: int = 1, end: int = 10) -> Node:
    return Node(qname, NodeType.FUNCTION, file_path, start, end)


def _make_cfg_with_edge(src_name: str, dst_name: str, src_file: str = "a.py", dst_file: str = "b.py") -> CallGraph:
    cfg = CallGraph(language="python")
    src = _make_node(src_name, src_file)
    dst = _make_node(dst_name, dst_file)
    cfg.add_node(src)
    cfg.add_node(dst)
    cfg.add_edge(src_name, dst_name)
    return cfg


def test_get_neighbors():
    cfg = _make_cfg_with_edge("caller", "callee")
    cfgs = {"python": cfg}

    upstream, downstream = _get_neighbors(cfgs, "callee")
    assert "caller" in upstream
    assert downstream == []

    upstream, downstream = _get_neighbors(cfgs, "caller")
    assert upstream == []
    assert "callee" in downstream


def test_get_neighbors_unknown_method():
    cfg = _make_cfg_with_edge("a", "b")
    upstream, downstream = _get_neighbors({"python": cfg}, "unknown")
    assert upstream == []
    assert downstream == []


def test_build_change_groups_modified_file(tmp_path):
    # Create a dummy source file
    src = tmp_path / "src" / "module.py"
    src.parent.mkdir(parents=True)
    src.write_text("def foo():\n    return 1\n")

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="src/module.py",
                file_status=ChangeStatus.MODIFIED,
                component_id="1",
                modified_methods=[
                    MethodChange(
                        qualified_name="module.foo",
                        file_path="src/module.py",
                        start_line=1,
                        end_line=2,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ],
    )

    groups = _build_change_groups(delta, {}, tmp_path, "HEAD")
    assert len(groups) == 1
    assert groups[0].group_key == "src/module.py"
    assert len(groups[0].methods) == 1
    assert groups[0].methods[0].qualified_name == "module.foo"


def test_build_change_groups_skips_empty_delta(tmp_path):
    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="src/empty.py",
                file_status=ChangeStatus.MODIFIED,
                component_id="1",
            )
        ],
    )
    groups = _build_change_groups(delta, {}, tmp_path, "HEAD")
    assert len(groups) == 0


def test_build_change_groups_deleted_methods(tmp_path):
    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="src/old.py",
                file_status=ChangeStatus.DELETED,
                component_id="1",
                deleted_methods=[
                    MethodChange(
                        qualified_name="old.func",
                        file_path="src/old.py",
                        start_line=1,
                        end_line=5,
                        change_type=ChangeStatus.DELETED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ],
    )
    # Deleted file with deleted methods should produce a group
    groups = _build_change_groups(delta, {}, tmp_path, "HEAD")
    assert len(groups) == 1
    assert groups[0].methods[0].change_type == ChangeStatus.DELETED


def test_build_initial_prompt():
    groups = [
        ChangeGroup(
            group_key="file.py",
            methods=[
                ChangedMethodContext(
                    qualified_name="file.func",
                    file_path="file.py",
                    change_type="modified",
                    new_body="def func(): pass",
                    diff_hunks="@@ -1,1 +1,1 @@\n-old\n+new",
                ),
            ],
            upstream_neighbors=["caller1"],
            downstream_neighbors=["callee1"],
        ),
    ]
    prompt = _build_initial_prompt(groups)
    assert "file.func" in prompt
    assert "def func(): pass" in prompt
    assert "caller1" in prompt
    assert "callee1" in prompt


def test_classify_scope():
    fci = FileComponentIndex(
        file_to_component={
            "src/auth.py": "1",
            "src/db.py": "2",
        }
    )

    # Mock static analysis to resolve methods to files
    static = MagicMock()

    def mock_get_reference(lang, qname):
        file_map = {
            "auth.login": _make_node("auth.login", "src/auth.py"),
            "db.query": _make_node("db.query", "src/db.py"),
        }
        if qname in file_map:
            return file_map[qname]
        raise ValueError("not found")

    static.get_languages.return_value = ["python"]
    static.get_reference.side_effect = mock_get_reference

    trace = TraceResult(
        all_impacted_methods=["auth.login", "db.query"],
        stop_reason=TraceStopReason.CLOSURE_REACHED,
    )

    result = classify_scope(trace, fci, static)
    assert len(result.impacted_components) == 2
    comp_ids = {ic.component_id for ic in result.impacted_components}
    assert comp_ids == {"1", "2"}


def test_classify_scope_unresolvable():
    fci = FileComponentIndex(file_to_component={"src/a.py": "1"})
    static = MagicMock()
    static.get_languages.return_value = ["python"]
    static.get_reference.side_effect = ValueError("not found")
    static.get_loose_reference.return_value = (None, None)

    trace = TraceResult(all_impacted_methods=["unknown.method"])
    result = classify_scope(trace, fci, static)
    assert result.impacted_components == []


def test_method_resolver_exact_match(tmp_path):
    src = tmp_path / "src" / "mod.py"
    src.parent.mkdir(parents=True)
    src.write_text("def hello():\n    return 'hi'\n")

    node = _make_node("mod.hello", str(src), 1, 2)
    static = MagicMock()
    static.get_languages.return_value = ["python"]
    static.get_reference.return_value = node

    resolver = MethodResolver(static, tmp_path)
    resolved_node, body = resolver.resolve("mod.hello")
    assert resolved_node is not None
    assert "def hello" in body
    assert resolver.unresolved == []


def test_method_resolver_unresolved(tmp_path):
    static = MagicMock()
    static.get_languages.return_value = ["python"]
    static.get_reference.side_effect = ValueError("not found")
    static.get_loose_reference.return_value = (None, None)

    resolver = MethodResolver(static, tmp_path)
    node, body = resolver.resolve("nonexistent.method")
    assert node is None
    assert body is None
    assert "nonexistent.method" in resolver.unresolved
