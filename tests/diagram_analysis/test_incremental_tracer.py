"""Tests for the incremental tracer and scope classification."""

import subprocess
from unittest.mock import MagicMock, patch

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
    _trace_message_content,
    classify_scope,
    run_trace,
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


def test_build_change_groups_strips_comments_from_changed_methods(tmp_path):
    src = tmp_path / "src" / "module.py"
    src.parent.mkdir(parents=True)
    src.write_text("def foo():\n    # old comment\n    return 1\n")

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
                        end_line=3,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ],
    )

    groups = _build_change_groups(delta, {}, {}, tmp_path, "HEAD")
    assert len(groups) == 1
    assert groups[0].methods[0].new_body is not None
    assert "# old comment" not in groups[0].methods[0].new_body


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
                ),
                ChangedMethodContext(
                    qualified_name="file.other",
                    file_path="file.py",
                    change_type="modified",
                    new_body="def other(): pass",
                ),
            ],
            upstream_neighbors=["caller1"],
            downstream_neighbors=["callee1"],
            diff_hunks="@@ -1,1 +1,1 @@\n-old\n+new",
        ),
    ]
    prompt = _build_initial_prompt(groups)
    assert "file.func" in prompt
    assert "file.other" in prompt
    assert "def func(): pass" in prompt
    assert "caller1" in prompt
    assert "callee1" in prompt
    assert prompt.count("```diff") == 1


def test_classify_scope(tmp_path):
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
            "auth.login": _make_node("auth.login", str(tmp_path / "src/auth.py")),
            "db.query": _make_node("db.query", str(tmp_path / "src/db.py")),
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

    result = classify_scope(trace, fci, static, repo_dir=tmp_path)
    assert len(result.impacted_components) == 2
    comp_ids = {ic.component_id for ic in result.impacted_components}
    assert comp_ids == {"1", "2"}


def test_classify_scope_unresolvable(tmp_path):
    fci = FileComponentIndex(file_to_component={"src/a.py": "1"})
    static = MagicMock()
    static.get_languages.return_value = ["python"]
    static.get_reference.side_effect = ValueError("not found")
    static.get_loose_reference.return_value = (None, None)

    trace = TraceResult(all_impacted_methods=["unknown.method"])
    result = classify_scope(trace, fci, static, repo_dir=tmp_path)
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


def test_method_resolver_strips_comments(tmp_path):
    src = tmp_path / "src" / "mod.py"
    src.parent.mkdir(parents=True)
    src.write_text("def hello():\n    # comment\n    return 'hi'\n")

    node = _make_node("mod.hello", str(src), 1, 3)
    static = MagicMock()
    static.get_languages.return_value = ["python"]
    static.get_reference.return_value = node

    resolver = MethodResolver(static, tmp_path)
    _, body = resolver.resolve("mod.hello")
    assert body is not None
    assert "# comment" not in body
    assert "return 'hi'" in body


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


def test_trace_message_content_adds_cache_for_anthropic():
    class FakeAnthropicLLM:
        __module__ = "langchain_anthropic.chat_models"

    content = _trace_message_content("hello", FakeAnthropicLLM(), enable_cache=True)
    assert isinstance(content, list)
    assert content[0]["text"] == "hello"
    assert content[0]["cache_control"] == {"type": "ephemeral"}


def test_trace_message_content_leaves_plain_text_for_other_models():
    content = _trace_message_content("hello", MagicMock(), enable_cache=True)
    assert content == "hello"


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


def test_build_change_groups_groups_connected_files_together(tmp_path):
    src_a = tmp_path / "src" / "a.py"
    src_b = tmp_path / "src" / "b.py"
    src_a.parent.mkdir(parents=True)
    src_a.write_text("def a():\n    return 1\n")
    src_b.write_text("def b():\n    return 2\n")

    cfg = _make_cfg_with_edge("pkg.a", "pkg.b", "src/a.py", "src/b.py")
    up_idx, down_idx = _build_neighbor_indexes({"python": cfg})

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="src/a.py",
                file_status=ChangeStatus.MODIFIED,
                component_id="1",
                modified_methods=[
                    MethodChange(
                        qualified_name="pkg.a",
                        file_path="src/a.py",
                        start_line=1,
                        end_line=2,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            ),
            FileDelta(
                file_path="src/b.py",
                file_status=ChangeStatus.MODIFIED,
                component_id="2",
                modified_methods=[
                    MethodChange(
                        qualified_name="pkg.b",
                        file_path="src/b.py",
                        start_line=1,
                        end_line=2,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            ),
        ]
    )

    groups = _build_change_groups(delta, up_idx, down_idx, tmp_path, "HEAD")
    assert len(groups) == 1
    assert set(groups[0].file_paths) == {"src/a.py", "src/b.py"}


def test_build_change_groups_keeps_disconnected_files_separate(tmp_path):
    src_a = tmp_path / "src" / "a.py"
    src_c = tmp_path / "src" / "c.py"
    src_a.parent.mkdir(parents=True)
    src_a.write_text("def a():\n    return 1\n")
    src_c.write_text("def c():\n    return 3\n")

    cfg = CallGraph(language="python")
    cfg.add_node(_make_node("pkg.a", "src/a.py"))
    cfg.add_node(_make_node("pkg.b", "src/b.py"))
    cfg.add_node(_make_node("pkg.c", "src/c.py"))
    cfg.add_node(_make_node("pkg.d", "src/d.py"))
    cfg.add_edge("pkg.a", "pkg.b")
    cfg.add_edge("pkg.c", "pkg.d")
    up_idx, down_idx = _build_neighbor_indexes({"python": cfg})

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="src/a.py",
                file_status=ChangeStatus.MODIFIED,
                component_id="1",
                modified_methods=[
                    MethodChange(
                        qualified_name="pkg.a",
                        file_path="src/a.py",
                        start_line=1,
                        end_line=2,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            ),
            FileDelta(
                file_path="src/c.py",
                file_status=ChangeStatus.MODIFIED,
                component_id="2",
                modified_methods=[
                    MethodChange(
                        qualified_name="pkg.c",
                        file_path="src/c.py",
                        start_line=1,
                        end_line=2,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            ),
        ]
    )

    groups = _build_change_groups(delta, up_idx, down_idx, tmp_path, "HEAD")
    assert len(groups) == 2
    assert {group.group_key for group in groups} == {"src/a.py", "src/c.py"}


def test_purely_additive_all_added():
    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="new.py",
                file_status=ChangeStatus.ADDED,
                added_methods=[
                    MethodChange(
                        qualified_name="new.func",
                        file_path="new.py",
                        start_line=1,
                        end_line=5,
                        change_type=ChangeStatus.ADDED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ]
    )
    assert delta.is_purely_additive is True


def test_not_purely_additive_modified():
    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="old.py",
                file_status=ChangeStatus.MODIFIED,
                modified_methods=[
                    MethodChange(
                        qualified_name="old.func",
                        file_path="old.py",
                        start_line=1,
                        end_line=5,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ]
    )
    assert delta.is_purely_additive is False


def test_not_purely_additive_deleted():
    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="gone.py",
                file_status=ChangeStatus.DELETED,
                deleted_methods=[
                    MethodChange(
                        qualified_name="gone.func",
                        file_path="gone.py",
                        start_line=1,
                        end_line=5,
                        change_type=ChangeStatus.DELETED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ]
    )
    assert delta.is_purely_additive is False


def test_purely_additive_new_methods_in_existing_file():
    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="existing.py",
                file_status=ChangeStatus.MODIFIED,
                added_methods=[
                    MethodChange(
                        qualified_name="existing.new_func",
                        file_path="existing.py",
                        start_line=10,
                        end_line=15,
                        change_type=ChangeStatus.ADDED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ]
    )
    assert delta.is_purely_additive is True


# ---------------------------------------------------------------------------
# Cosmetic change skipping in _build_change_groups
# ---------------------------------------------------------------------------


def _make_modified_delta(file_path: str = "src/module.py") -> IncrementalDelta:
    return IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path=file_path,
                file_status=ChangeStatus.MODIFIED,
                component_id="1",
                modified_methods=[
                    MethodChange(
                        qualified_name="module.foo",
                        file_path=file_path,
                        start_line=1,
                        end_line=2,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ],
    )


@patch("diagram_analysis.incremental_tracer.is_file_cosmetic", return_value=True)
def test_build_change_groups_skips_cosmetic_file(mock_cosmetic, tmp_path):
    src = tmp_path / "src" / "module.py"
    src.parent.mkdir(parents=True)
    src.write_text("def foo():\n    return 1\n")

    delta = _make_modified_delta()
    groups = _build_change_groups(delta, {}, {}, tmp_path, "HEAD")
    assert len(groups) == 0
    mock_cosmetic.assert_called_once()


@patch("diagram_analysis.incremental_tracer.is_file_cosmetic", return_value=False)
def test_build_change_groups_keeps_semantic_file(mock_cosmetic, tmp_path):
    src = tmp_path / "src" / "module.py"
    src.parent.mkdir(parents=True)
    src.write_text("def foo():\n    return 1\n")

    delta = _make_modified_delta()
    groups = _build_change_groups(delta, {}, {}, tmp_path, "HEAD")
    assert len(groups) == 1
    mock_cosmetic.assert_called_once()


@patch("diagram_analysis.incremental_tracer.is_file_cosmetic")
def test_build_change_groups_no_cosmetic_check_for_added(mock_cosmetic, tmp_path):
    src = tmp_path / "src" / "new.py"
    src.parent.mkdir(parents=True)
    src.write_text("def bar():\n    return 2\n")

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="src/new.py",
                file_status=ChangeStatus.ADDED,
                component_id="1",
                added_methods=[
                    MethodChange(
                        qualified_name="new.bar",
                        file_path="src/new.py",
                        start_line=1,
                        end_line=2,
                        change_type=ChangeStatus.ADDED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ],
    )
    groups = _build_change_groups(delta, {}, {}, tmp_path, "HEAD")
    assert len(groups) == 1
    mock_cosmetic.assert_not_called()


@patch("diagram_analysis.incremental_tracer.is_file_cosmetic")
def test_build_change_groups_no_cosmetic_check_for_deleted(mock_cosmetic, tmp_path):
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
    groups = _build_change_groups(delta, {}, {}, tmp_path, "HEAD")
    assert len(groups) == 1
    mock_cosmetic.assert_not_called()


@patch("diagram_analysis.incremental_tracer.is_file_cosmetic", return_value=True)
def test_build_change_groups_no_skip_when_methods_added(mock_cosmetic, tmp_path):
    """A MODIFIED file with both added and modified methods should not be skipped."""
    src = tmp_path / "src" / "module.py"
    src.parent.mkdir(parents=True)
    src.write_text("def foo():\n    return 1\ndef bar():\n    return 2\n")

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="src/module.py",
                file_status=ChangeStatus.MODIFIED,
                component_id="1",
                added_methods=[
                    MethodChange(
                        qualified_name="module.bar",
                        file_path="src/module.py",
                        start_line=3,
                        end_line=4,
                        change_type=ChangeStatus.ADDED,
                        node_type="FUNCTION",
                    )
                ],
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
    mock_cosmetic.assert_not_called()


def _init_git_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True, check=True)


def _commit_file(repo, file_path: str, content: str, message: str = "c") -> str:
    full = repo / file_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    subprocess.run(["git", "add", file_path], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, capture_output=True, check=True)
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def test_build_change_groups_filters_semantically_unchanged_methods(tmp_path):
    _init_git_repo(tmp_path)
    base = _commit_file(
        tmp_path,
        "src/module.py",
        "def foo():\n    return 1\n\ndef bar():\n    return 2\n",
    )
    (tmp_path / "src" / "module.py").write_text(
        "def foo():\n    # comment only\n    return 1\n\ndef bar():\n    return 3\n"
    )

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
                        end_line=3,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    ),
                    MethodChange(
                        qualified_name="module.bar",
                        file_path="src/module.py",
                        start_line=5,
                        end_line=6,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    ),
                ],
            )
        ]
    )

    groups = _build_change_groups(delta, {}, {}, tmp_path, base)
    assert len(groups) == 1
    assert [method.qualified_name for method in groups[0].methods] == ["module.bar"]


def test_run_trace_uses_deterministic_fast_path_without_llm(tmp_path):
    _init_git_repo(tmp_path)
    base = _commit_file(tmp_path, "src/module.py", "def foo():\n    return 1\n")
    (tmp_path / "src" / "module.py").write_text("def foo():\n    return 2\n")

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
        ]
    )

    result = run_trace(
        delta=delta,
        cfgs={},
        static_analysis=MagicMock(),
        repo_dir=tmp_path,
        base_ref=base,
        agent_llm=MagicMock(),
        parsing_llm=MagicMock(),
    )

    assert result.stop_reason == TraceStopReason.CLOSURE_REACHED
    assert result.all_impacted_methods == ["module.foo"]


@patch("diagram_analysis.incremental_tracer._trace_single_group")
def test_run_trace_parallelizes_independent_regions(mock_trace_single_group, tmp_path):
    src_a = tmp_path / "src" / "a.py"
    src_c = tmp_path / "src" / "c.py"
    src_a.parent.mkdir(parents=True)
    src_a.write_text("def a():\n    return 1\n")
    src_c.write_text("def c():\n    return 3\n")

    cfg = CallGraph(language="python")
    cfg.add_node(_make_node("pkg.a", "src/a.py"))
    cfg.add_node(_make_node("pkg.b", "src/b.py"))
    cfg.add_node(_make_node("pkg.c", "src/c.py"))
    cfg.add_node(_make_node("pkg.d", "src/d.py"))
    cfg.add_edge("pkg.a", "pkg.b")
    cfg.add_edge("pkg.c", "pkg.d")

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="src/a.py",
                file_status=ChangeStatus.MODIFIED,
                component_id="1",
                modified_methods=[
                    MethodChange(
                        qualified_name="pkg.a",
                        file_path="src/a.py",
                        start_line=1,
                        end_line=2,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            ),
            FileDelta(
                file_path="src/c.py",
                file_status=ChangeStatus.MODIFIED,
                component_id="2",
                modified_methods=[
                    MethodChange(
                        qualified_name="pkg.c",
                        file_path="src/c.py",
                        start_line=1,
                        end_line=2,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            ),
        ]
    )

    mock_trace_single_group.side_effect = [
        TraceResult(all_impacted_methods=["pkg.a"], stop_reason=TraceStopReason.CLOSURE_REACHED, hops_used=1),
        TraceResult(all_impacted_methods=["pkg.c"], stop_reason=TraceStopReason.CLOSURE_REACHED, hops_used=2),
    ]

    result = run_trace(
        delta=delta,
        cfgs={"python": cfg},
        static_analysis=MagicMock(),
        repo_dir=tmp_path,
        base_ref="HEAD",
        agent_llm=MagicMock(),
        parsing_llm=MagicMock(),
    )

    assert mock_trace_single_group.call_count == 2
    assert result.stop_reason == TraceStopReason.CLOSURE_REACHED
    assert result.all_impacted_methods == ["pkg.a", "pkg.c"]
    assert result.hops_used == 2


# ---------------------------------------------------------------------------
# File extension filtering
# ---------------------------------------------------------------------------


def test_build_change_groups_skips_non_analyzable_extensions(tmp_path):
    """Files with non-analyzable extensions (e.g. .yml, .md) are skipped entirely."""
    cfg_file = tmp_path / "config.yml"
    cfg_file.write_text("key: value\n")

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="config.yml",
                file_status=ChangeStatus.MODIFIED,
                component_id="1",
                modified_methods=[
                    MethodChange(
                        qualified_name="config.key",
                        file_path="config.yml",
                        start_line=1,
                        end_line=1,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ],
    )
    groups = _build_change_groups(delta, {}, {}, tmp_path, "HEAD")
    assert len(groups) == 0


def test_build_change_groups_keeps_analyzable_extensions(tmp_path):
    """Files with analyzable extensions (e.g. .py) are kept."""
    src = tmp_path / "module.py"
    src.write_text("def foo():\n    return 1\n")

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="module.py",
                file_status=ChangeStatus.MODIFIED,
                component_id="1",
                modified_methods=[
                    MethodChange(
                        qualified_name="module.foo",
                        file_path="module.py",
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


def test_build_change_groups_mixed_extensions(tmp_path):
    """Only analyzable files produce groups when mixed with non-analyzable."""
    src = tmp_path / "module.py"
    src.write_text("def foo():\n    return 1\n")
    cfg_file = tmp_path / "config.yml"
    cfg_file.write_text("key: value\n")

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="module.py",
                file_status=ChangeStatus.MODIFIED,
                component_id="1",
                modified_methods=[
                    MethodChange(
                        qualified_name="module.foo",
                        file_path="module.py",
                        start_line=1,
                        end_line=2,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            ),
            FileDelta(
                file_path="config.yml",
                file_status=ChangeStatus.MODIFIED,
                component_id="1",
                modified_methods=[
                    MethodChange(
                        qualified_name="config.key",
                        file_path="config.yml",
                        start_line=1,
                        end_line=1,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            ),
        ],
    )
    groups = _build_change_groups(delta, {}, {}, tmp_path, "HEAD")
    assert len(groups) == 1
    assert groups[0].methods[0].file_path == "module.py"


# ---------------------------------------------------------------------------
# Syntax error gating
# ---------------------------------------------------------------------------


def test_run_trace_aborts_on_syntax_error(tmp_path):
    """Incremental trace aborts when a changed file has syntax errors."""
    src = tmp_path / "broken.py"
    src.write_text("def foo(\n    return 1\n")  # missing closing paren

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="broken.py",
                file_status=ChangeStatus.MODIFIED,
                component_id="1",
                modified_methods=[
                    MethodChange(
                        qualified_name="broken.foo",
                        file_path="broken.py",
                        start_line=1,
                        end_line=2,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ],
    )

    result = run_trace(
        delta=delta,
        cfgs={},
        static_analysis=MagicMock(),
        repo_dir=tmp_path,
        base_ref="HEAD",
        agent_llm=MagicMock(),
        parsing_llm=MagicMock(),
    )

    assert result.stop_reason == TraceStopReason.SYNTAX_ERROR
    assert result.all_impacted_methods == []


def test_run_trace_skips_syntax_check_for_deleted_files(tmp_path):
    """Deleted files are not syntax-checked (they don't exist on disk)."""
    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="gone.py",
                file_status=ChangeStatus.DELETED,
                component_id="1",
                deleted_methods=[
                    MethodChange(
                        qualified_name="gone.func",
                        file_path="gone.py",
                        start_line=1,
                        end_line=5,
                        change_type=ChangeStatus.DELETED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ],
    )

    result = run_trace(
        delta=delta,
        cfgs={},
        static_analysis=MagicMock(),
        repo_dir=tmp_path,
        base_ref="HEAD",
        agent_llm=MagicMock(),
        parsing_llm=MagicMock(),
    )

    # Should not abort — deleted files can't be syntax-checked
    assert result.stop_reason != TraceStopReason.SYNTAX_ERROR


def test_run_trace_skips_syntax_check_for_non_analyzable(tmp_path):
    """Non-analyzable files are not syntax-checked."""
    cfg_file = tmp_path / "config.yml"
    cfg_file.write_text("key: {{{invalid yaml maybe\n")

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="config.yml",
                file_status=ChangeStatus.MODIFIED,
                component_id="1",
                modified_methods=[
                    MethodChange(
                        qualified_name="config.key",
                        file_path="config.yml",
                        start_line=1,
                        end_line=1,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ],
    )

    result = run_trace(
        delta=delta,
        cfgs={},
        static_analysis=MagicMock(),
        repo_dir=tmp_path,
        base_ref="HEAD",
        agent_llm=MagicMock(),
        parsing_llm=MagicMock(),
    )

    assert result.stop_reason != TraceStopReason.SYNTAX_ERROR


def test_run_trace_passes_with_valid_syntax(tmp_path):
    """Valid Python files pass the syntax gate and proceed to tracing."""
    src = tmp_path / "valid.py"
    src.write_text("def foo():\n    return 1\n")

    delta = IncrementalDelta(
        file_deltas=[
            FileDelta(
                file_path="valid.py",
                file_status=ChangeStatus.MODIFIED,
                component_id="1",
                modified_methods=[
                    MethodChange(
                        qualified_name="valid.foo",
                        file_path="valid.py",
                        start_line=1,
                        end_line=2,
                        change_type=ChangeStatus.MODIFIED,
                        node_type="FUNCTION",
                    )
                ],
            )
        ],
    )

    result = run_trace(
        delta=delta,
        cfgs={},
        static_analysis=MagicMock(),
        repo_dir=tmp_path,
        base_ref="HEAD",
        agent_llm=MagicMock(),
        parsing_llm=MagicMock(),
    )

    assert result.stop_reason != TraceStopReason.SYNTAX_ERROR
