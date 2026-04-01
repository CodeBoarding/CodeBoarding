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
    _build_neighbor_indexes,
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
    up_idx, down_idx = _build_neighbor_indexes({"python": cfg})

    upstream, downstream = _get_neighbors(up_idx, down_idx, "callee")
    assert "caller" in upstream
    assert downstream == []

    upstream, downstream = _get_neighbors(up_idx, down_idx, "caller")
    assert upstream == []
    assert "callee" in downstream


def test_get_neighbors_unknown_method():
    cfg = _make_cfg_with_edge("a", "b")
    up_idx, down_idx = _build_neighbor_indexes({"python": cfg})
    upstream, downstream = _get_neighbors(up_idx, down_idx, "unknown")
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

    groups = _build_change_groups(delta, {}, {}, tmp_path, "HEAD")
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
    groups = _build_change_groups(delta, {}, {}, tmp_path, "HEAD")
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
    groups = _build_change_groups(delta, {}, {}, tmp_path, "HEAD")
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
    assert body is not None
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


# ---------------------------------------------------------------------------
# Neighbor index tests
# ---------------------------------------------------------------------------


def test_build_neighbor_indexes():
    """Indexes from a single CFG give correct O(1) lookups."""
    cfg = CallGraph(language="python")
    a = _make_node("a", "a.py")
    b = _make_node("b", "b.py")
    c = _make_node("c", "c.py")
    cfg.add_node(a)
    cfg.add_node(b)
    cfg.add_node(c)
    cfg.add_edge("a", "b")
    cfg.add_edge("a", "c")
    cfg.add_edge("b", "c")

    up_idx, down_idx = _build_neighbor_indexes({"python": cfg})

    # a calls b and c
    assert sorted(down_idx["a"]) == ["b", "c"]
    # b calls c
    assert down_idx["b"] == ["c"]
    # c calls nothing
    assert "c" not in down_idx

    # c is called by a and b
    assert sorted(up_idx["c"]) == ["a", "b"]
    # b is called by a
    assert up_idx["b"] == ["a"]
    # a is not called by anyone
    assert "a" not in up_idx


def test_build_neighbor_indexes_with_baseline():
    """Baseline CFGs contribute edges for deleted methods not in current CFGs."""
    current_cfg = CallGraph(language="python")
    a = _make_node("a", "a.py")
    b = _make_node("b", "b.py")
    current_cfg.add_node(a)
    current_cfg.add_node(b)
    current_cfg.add_edge("a", "b")

    baseline_cfg = CallGraph(language="python")
    deleted = _make_node("deleted_func", "old.py")
    c = _make_node("c", "c.py")
    baseline_cfg.add_node(deleted)
    baseline_cfg.add_node(c)
    baseline_cfg.add_edge("deleted_func", "c")
    baseline_cfg.add_edge("c", "deleted_func")

    up_idx, down_idx = _build_neighbor_indexes({"python": current_cfg}, {"python": baseline_cfg})

    # deleted_func downstream from baseline
    assert "c" in down_idx["deleted_func"]
    # deleted_func upstream from baseline
    assert "c" in up_idx["deleted_func"]
    # current CFG edges still present
    assert "b" in down_idx["a"]


def test_deleted_method_gets_neighbors(tmp_path):
    """Deleted methods should get neighbor context via indexes."""
    baseline_cfg = CallGraph(language="python")
    caller = _make_node("caller", "mod.py")
    deleted = _make_node("deleted_func", "old.py")
    baseline_cfg.add_node(caller)
    baseline_cfg.add_node(deleted)
    baseline_cfg.add_edge("caller", "deleted_func")

    up_idx, down_idx = _build_neighbor_indexes({}, {"python": baseline_cfg})

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="old.py",
                file_status=ChangeStatus.DELETED,
                component_id="1",
                deleted_methods=[
                    MethodChange(
                        qualified_name="deleted_func",
                        file_path="old.py",
                        start_line=1,
                        end_line=5,
                        change_type=ChangeStatus.DELETED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ],
    )
    groups = _build_change_groups(delta, up_idx, down_idx, tmp_path, "HEAD")
    assert len(groups) == 1
    assert "caller" in groups[0].upstream_neighbors


def test_build_change_groups_uses_indexes(tmp_path):
    """_build_change_groups uses pre-built indexes instead of raw cfgs."""
    src = tmp_path / "src" / "module.py"
    src.parent.mkdir(parents=True)
    src.write_text("def foo():\n    return 1\n")

    cfg = _make_cfg_with_edge("module.foo", "module.bar")
    up_idx, down_idx = _build_neighbor_indexes({"python": cfg})

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

    groups = _build_change_groups(delta, up_idx, down_idx, tmp_path, "HEAD")
    assert len(groups) == 1
    assert "module.bar" in groups[0].downstream_neighbors
