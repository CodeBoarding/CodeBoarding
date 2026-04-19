import pytest

from static_analyzer.cfg_skip_planner import ContextBudgetExceededError, plan_skip_set
from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph, ClusterResult
from static_analyzer.node import Node


def _add_node(cfg: CallGraph, name: str, line: int) -> None:
    cfg.add_node(Node(name, NodeType.FUNCTION, "/src/mod.py", line, line + 1))


def test_plan_skip_set_returns_empty_when_render_fits():
    cfg = CallGraph(language="python")
    for line, name in enumerate(("mod.a", "mod.b", "mod.c"), start=1):
        _add_node(cfg, name, line)
    cfg.add_edge("mod.a", "mod.b")
    cfg.add_edge("mod.b", "mod.c")
    cluster_result = ClusterResult(clusters={1: {"mod.a", "mod.b", "mod.c"}}, strategy="test")

    full = cfg.to_cluster_string(cluster_result=cluster_result)

    assert plan_skip_set(cfg, cluster_result, len(full) + 1) == set()


def test_plan_skip_set_raises_when_no_peel_safe_cluster_member_can_fit():
    cfg = CallGraph(language="python")
    for line, name in enumerate(("mod.a", "mod.b", "mod.c"), start=1):
        _add_node(cfg, name, line)
    cfg.add_edge("mod.a", "mod.b")
    cfg.add_edge("mod.b", "mod.c")
    cfg.add_edge("mod.c", "mod.a")
    cluster_result = ClusterResult(clusters={1: {"mod.a", "mod.b", "mod.c"}}, strategy="test")

    full = cfg.to_cluster_string(cluster_result=cluster_result)

    with pytest.raises(ContextBudgetExceededError):
        plan_skip_set(cfg, cluster_result, len(full) - 1)


def test_plan_skip_set_can_choose_later_high_savings_leaf_under_cap():
    cfg = CallGraph(language="python")
    expensive = "mod." + "very_long_function_name_" * 20
    for line, name in enumerate(("mod.cheap_a", "mod.cheap_b", expensive, "mod.center"), start=1):
        _add_node(cfg, name, line)
    cfg.add_edge("mod.center", "mod.cheap_a")
    cfg.add_edge("mod.center", "mod.cheap_b")
    cfg.add_edge("mod.center", expensive)
    cluster_result = ClusterResult(
        clusters={1: {"mod.cheap_a", "mod.cheap_b", expensive, "mod.center"}},
        strategy="test",
    )

    full = cfg.to_cluster_string(cluster_result=cluster_result)
    expensive_skip_len = len(cfg.to_cluster_string(cluster_result=cluster_result, skip_nodes={expensive}))
    cheap_skip_len = len(cfg.to_cluster_string(cluster_result=cluster_result, skip_nodes={"mod.cheap_a"}))
    char_budget = expensive_skip_len + 5
    assert cheap_skip_len > char_budget
    assert len(full) > char_budget

    skip = plan_skip_set(
        cfg,
        cluster_result,
        char_budget,
        max_peel_frac=0.25,
        min_keep_per_cluster=1,
    )

    assert skip == {expensive}
    assert len(cfg.to_cluster_string(cluster_result=cluster_result, skip_nodes=skip)) <= char_budget
