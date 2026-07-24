"""The deterministic scope planner: what survives a re-clustering, and what may not.

A scope's leaf clusters are re-derived from its subgraph on every run, so their integer
ids are not an identity — they renumber whenever the code inside the scope changes.
These tests pin the planner to the anchor that does survive: the methods themselves.
"""

import unittest

import networkx as nx

from agents.agent_responses import AnalysisInsights, Component, ScopeOperationAction
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.scope_ids import ROOT_SCOPE_ID
from diagram_analysis.scope_plan import plan_scope_update, previous_ownership
from static_analyzer.graph import ClusterResult

FILE = "pkg/mod.py"


def method(qualified_name: str) -> MethodEntry:
    return MethodEntry(qualified_name=qualified_name, node_type="function", start_line=1, end_line=2)


def component(component_id: str, qnames: list[str], cluster_ids: list[str]) -> Component:
    return Component(
        name=f"C{component_id}",
        description="",
        key_entities=[],
        component_id=component_id,
        source_cluster_ids=cluster_ids,
        file_methods=[FileMethodGroup(file_path=FILE, methods=[method(q) for q in qnames])],
    )


def clustering(clusters: dict[int, set[str]]) -> ClusterResult:
    return ClusterResult(
        clusters=clusters,
        cluster_to_files={cid: {FILE} for cid in clusters},
        file_to_clusters={FILE: set(clusters)},
        strategy="test",
    )


def cfg_for(clusters: dict[int, set[str]]) -> nx.DiGraph:
    """A graph with an edge inside each cluster and one bridge between consecutive ones."""
    graph = nx.DiGraph()
    for members in clusters.values():
        ordered = sorted(members)
        for node in ordered:
            graph.add_node(node, file_path=FILE)
        for src, dst in zip(ordered, ordered[1:]):
            graph.add_edge(src, dst)
    heads = [sorted(members)[0] for _cid, members in sorted(clusters.items())]
    for src, dst in zip(heads, heads[1:]):
        graph.add_edge(src, dst)
    return graph


def actions(decision) -> dict[str, ScopeOperationAction]:
    """component_id (or the created name) -> the action planned for it."""
    return {op.component_id or op.name: op.action for op in decision.operations}


class TestPreviousOwnership(unittest.TestCase):
    def test_a_cluster_belongs_to_whoever_owned_most_of_its_methods(self):
        scope = AnalysisInsights(
            description="",
            components=[component("1", ["a.one", "a.two"], ["1"]), component("2", ["b.one"], ["2"])],
            components_relations=[],
        )

        owner = previous_ownership(scope, clustering({7: {"a.one", "a.two", "b.one"}}))

        self.assertEqual(owner, {7: "1"})

    def test_a_cluster_of_entirely_new_methods_has_no_owner(self):
        scope = AnalysisInsights(description="", components=[component("1", ["a.one"], ["1"])], components_relations=[])

        owner = previous_ownership(scope, clustering({1: {"a.one"}, 2: {"fresh.thing"}}))

        self.assertEqual(owner, {1: "1"})

    def test_an_even_split_resolves_to_the_lowest_component_id(self):
        # Whichever way it goes it must go the same way on every run, or a rerun
        # with no source change would hand the cluster to a different component.
        scope = AnalysisInsights(
            description="",
            components=[component("2", ["b.one"], ["2"]), component("1", ["a.one"], ["1"])],
            components_relations=[],
        )

        owner = previous_ownership(scope, clustering({5: {"a.one", "b.one"}}))

        self.assertEqual(owner, {5: "1"})


class TestPlanScopeUpdate(unittest.TestCase):
    def test_renumbered_clusters_still_carry_every_component_forward(self):
        # The failure this anchor exists to prevent: a sub-scope is re-clustered from
        # scratch, its ids come back different, and every component looks brand new.
        members = {"a.one", "a.two", "b.one", "b.two", "c.one", "c.two"}
        scope = AnalysisInsights(
            description="",
            components=[
                component("2.1", ["a.one", "a.two"], ["2.1"]),
                component("2.2", ["b.one", "b.two"], ["2.2"]),
                component("2.3", ["c.one", "c.two"], ["2.3"]),
            ],
            components_relations=[],
        )
        renumbered = {
            41: {"a.one", "a.two"},
            42: {"b.one", "b.two"},
            43: {"c.one", "c.two"},
        }
        self.assertFalse({str(cid) for cid in renumbered} & set(members))

        decision = plan_scope_update("2", scope, {"python": clustering(renumbered)}, {"python": cfg_for(renumbered)})

        self.assertEqual(
            actions(decision),
            {
                "2.1": ScopeOperationAction.UPDATE_COMPONENT,
                "2.2": ScopeOperationAction.UPDATE_COMPONENT,
                "2.3": ScopeOperationAction.UPDATE_COMPONENT,
            },
        )

    def test_an_untouched_scope_plans_no_creation_and_no_deletion(self):
        clusters = {1: {"a.one"}, 2: {"b.one"}, 3: {"c.one"}}
        scope = AnalysisInsights(
            description="",
            components=[
                component("1", ["a.one"], ["1"]),
                component("2", ["b.one"], ["2"]),
                component("3", ["c.one"], ["3"]),
            ],
            components_relations=[],
        )

        decision = plan_scope_update(
            ROOT_SCOPE_ID, scope, {"python": clustering(clusters)}, {"python": cfg_for(clusters)}
        )

        self.assertEqual(
            {op.action for op in decision.operations},
            {ScopeOperationAction.UPDATE_COMPONENT},
        )

    def test_a_component_whose_methods_all_vanished_is_deleted(self):
        clusters = {1: {"a.one"}, 2: {"b.one"}, 3: {"c.one"}}
        scope = AnalysisInsights(
            description="",
            components=[
                component("1", ["a.one"], ["1"]),
                component("2", ["b.one"], ["2"]),
                component("3", ["c.one"], ["3"]),
                component("4", ["gone.one", "gone.two"], ["4"]),
            ],
            components_relations=[],
        )

        decision = plan_scope_update(
            ROOT_SCOPE_ID, scope, {"python": clustering(clusters)}, {"python": cfg_for(clusters)}
        )

        self.assertEqual(actions(decision)["4"], ScopeOperationAction.DELETE_COMPONENT)

    def test_new_methods_join_an_existing_component_rather_than_founding_one(self):
        clusters = {1: {"a.one"}, 2: {"b.one"}, 3: {"c.one"}, 4: {"a.three"}}
        scope = AnalysisInsights(
            description="",
            components=[
                component("1", ["a.one"], ["1"]),
                component("2", ["b.one"], ["2"]),
                component("3", ["c.one"], ["3"]),
            ],
            components_relations=[],
        )

        decision = plan_scope_update(
            ROOT_SCOPE_ID, scope, {"python": clustering(clusters)}, {"python": cfg_for(clusters)}
        )

        self.assertNotIn(ScopeOperationAction.CREATE_COMPONENT, {op.action for op in decision.operations})
        owned = {ref.cluster_id for op in decision.operations for ref in op.cluster_refs}
        self.assertEqual(owned, set(clusters))

    def test_every_planned_ref_is_tagged_with_its_language(self):
        clusters = {1: {"a.one"}, 2: {"b.one"}, 3: {"c.one"}}
        scope = AnalysisInsights(
            description="",
            components=[component(str(i), [q], [str(i)]) for i, q in enumerate(["a.one", "b.one", "c.one"], start=1)],
            components_relations=[],
        )

        decision = plan_scope_update(
            ROOT_SCOPE_ID, scope, {"python": clustering(clusters)}, {"python": cfg_for(clusters)}
        )

        for op in decision.operations:
            for ref in op.cluster_refs:
                self.assertEqual(ref.language, "python")
                self.assertEqual(ref.scope_id, ROOT_SCOPE_ID)

    def test_an_empty_clustering_plans_nothing(self):
        scope = AnalysisInsights(description="", components=[component("1", ["a.one"], ["1"])], components_relations=[])

        decision = plan_scope_update(ROOT_SCOPE_ID, scope, {"python": clustering({})}, {})

        self.assertEqual(decision.operations, [])


if __name__ == "__main__":
    unittest.main()
