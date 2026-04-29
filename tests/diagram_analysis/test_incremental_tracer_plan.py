"""Tests for the deterministic parts of the incremental tracer.

These exercise the build_trace_plan path (cosmetic filtering, region grouping,
fallback-group collapsing) without invoking the LLM-backed tracing loop.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
from textwrap import dedent

import pytest

from agents.change_status import ChangeStatus
from diagram_analysis.incremental_tracer import (
    ChangeGroup,
    _collapse_fallback_groups,
    build_trace_plan,
)
from diagram_analysis.incremental_updater import FileDelta, IncrementalDelta, MethodChange
from repo_utils.change_detector import ChangeSet
from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _make_repo_with_file(contents: str, file_rel: str = "src/utils.py") -> tuple[Path, str]:
    tmp = Path(tempfile.mkdtemp(prefix="cb_tracer_plan_"))
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    path = repo / file_rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    base_ref = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, base_ref


def _make_method_change(
    qualified_name: str, file_path: str, start: int, end: int, change_type: ChangeStatus
) -> MethodChange:
    return MethodChange(
        qualified_name=qualified_name,
        file_path=file_path,
        start_line=start,
        end_line=end,
        change_type=change_type,
        node_type="FUNCTION",
    )


def _make_cfg(nodes: dict[str, Node], edges: list[tuple[str, str]], language: str = "python") -> CallGraph:
    cfg = CallGraph(language=language)
    for node in nodes.values():
        cfg.add_node(node)
    for src, dst in edges:
        cfg.add_edge(src, dst)
    return cfg


@pytest.mark.integration
def test_cosmetic_only_modification_is_skipped_from_plan():
    baseline = dedent(
        """\
        def alpha() -> int:
            return 1
        """
    )
    repo, base_ref = _make_repo_with_file(baseline)
    try:
        # Add only a comment — cosmetic
        cosmetic = "# a new comment\n" + baseline
        (repo / "src" / "utils.py").write_text(cosmetic)

        delta = IncrementalDelta(
            file_deltas=[
                FileDelta(
                    file_path="src/utils.py",
                    file_status=ChangeStatus.MODIFIED,
                    component_id="1",
                    modified_methods=[
                        _make_method_change("src.utils.alpha", "src/utils.py", 2, 3, ChangeStatus.MODIFIED),
                    ],
                )
            ]
        )

        plan = build_trace_plan(
            delta=delta,
            cfgs={},
            repo_dir=repo,
            base_ref=base_ref,
            parsed_diff=ChangeSet(base_ref=base_ref, target_ref=""),
        )
        # Cosmetic-only file should contribute no groups
        assert plan.groups == []
        assert plan.fast_path_impacted_methods == []
        assert plan.cosmetic_skipped == 1
    finally:
        shutil.rmtree(repo.parent, ignore_errors=True)


def test_collapse_fallback_groups_keeps_graph_backed_independent():
    # Three groups: two fallback (not graph-backed), one graph-backed
    fb1 = ChangeGroup(group_key="file:a.py", graph_backed=False, file_paths=["a.py"])
    fb2 = ChangeGroup(group_key="file:b.py", graph_backed=False, file_paths=["b.py"])
    gb = ChangeGroup(group_key="region:0", graph_backed=True, file_paths=["c.py"])

    collapsed = _collapse_fallback_groups([fb1, fb2, gb])

    # The graph-backed group should still be there, plus one combined fallback
    assert len(collapsed) == 2
    graph_backed_groups = [g for g in collapsed if g.graph_backed]
    fallback_groups = [g for g in collapsed if not g.graph_backed]
    assert len(graph_backed_groups) == 1
    assert len(fallback_groups) == 1
    assert set(fallback_groups[0].file_paths) == {"a.py", "b.py"}


def test_collapse_fallback_groups_with_single_fallback_is_noop():
    fb = ChangeGroup(group_key="file:a.py", graph_backed=False, file_paths=["a.py"])
    gb = ChangeGroup(group_key="region:0", graph_backed=True, file_paths=["c.py"])
    collapsed = _collapse_fallback_groups([fb, gb])
    assert collapsed == [fb, gb]


@pytest.mark.integration
def test_plan_builds_groups_for_modified_method_with_callers():
    baseline = dedent(
        """\
        def helper() -> int:
            return 0


        def caller() -> int:
            return helper() + 1
        """
    )
    repo, base_ref = _make_repo_with_file(baseline)
    try:
        changed = dedent(
            """\
            def helper() -> int:
                return 42


            def caller() -> int:
                return helper() + 1
            """
        )
        (repo / "src" / "utils.py").write_text(changed)

        helper = Node(
            fully_qualified_name="src.utils.helper",
            node_type=NodeType.FUNCTION,
            file_path="src/utils.py",
            line_start=1,
            line_end=2,
        )
        caller = Node(
            fully_qualified_name="src.utils.caller",
            node_type=NodeType.FUNCTION,
            file_path="src/utils.py",
            line_start=5,
            line_end=6,
        )
        cfg = _make_cfg({"h": helper, "c": caller}, [("src.utils.caller", "src.utils.helper")])

        delta = IncrementalDelta(
            file_deltas=[
                FileDelta(
                    file_path="src/utils.py",
                    file_status=ChangeStatus.MODIFIED,
                    component_id="1",
                    modified_methods=[
                        _make_method_change("src.utils.helper", "src/utils.py", 1, 2, ChangeStatus.MODIFIED),
                    ],
                )
            ]
        )

        plan = build_trace_plan(
            delta=delta,
            cfgs={"python": cfg},
            repo_dir=repo,
            base_ref=base_ref,
            parsed_diff=ChangeSet(base_ref=base_ref, target_ref=""),
        )
        # Helper was modified (with a real body change), has an upstream caller -> should NOT be fast-path
        assert plan.fast_path_impacted_methods == []
        assert len(plan.groups) == 1
        group = plan.groups[0]
        assert "src.utils.caller" in group.upstream_neighbors
        assert {m.qualified_name for m in group.methods} == {"src.utils.helper"}
    finally:
        shutil.rmtree(repo.parent, ignore_errors=True)


@pytest.mark.integration
def test_plan_fast_path_used_for_leaf_modification_with_no_callers():
    baseline = dedent(
        """\
        def isolated() -> int:
            return 1
        """
    )
    repo, base_ref = _make_repo_with_file(baseline)
    try:
        changed = dedent(
            """\
            def isolated() -> int:
                return 42
            """
        )
        (repo / "src" / "utils.py").write_text(changed)

        node = Node(
            fully_qualified_name="src.utils.isolated",
            node_type=NodeType.FUNCTION,
            file_path="src/utils.py",
            line_start=1,
            line_end=2,
        )
        cfg = _make_cfg({"x": node}, [])

        delta = IncrementalDelta(
            file_deltas=[
                FileDelta(
                    file_path="src/utils.py",
                    file_status=ChangeStatus.MODIFIED,
                    component_id="1",
                    modified_methods=[
                        _make_method_change("src.utils.isolated", "src/utils.py", 1, 2, ChangeStatus.MODIFIED),
                    ],
                )
            ]
        )

        plan = build_trace_plan(
            delta=delta,
            cfgs={"python": cfg},
            repo_dir=repo,
            base_ref=base_ref,
            parsed_diff=ChangeSet(base_ref=base_ref, target_ref=""),
        )
        # No callers, signature unchanged, body-only change -> fast path
        assert plan.fast_path_impacted_methods == ["src.utils.isolated"]
        assert plan.groups == []
    finally:
        shutil.rmtree(repo.parent, ignore_errors=True)
