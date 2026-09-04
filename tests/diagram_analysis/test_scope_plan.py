"""The deterministic scope planner: what survives a re-clustering, and what may not.

A scope's leaf clusters are re-derived from its subgraph on every run, so their integer
ids are not an identity — they renumber whenever the code inside the scope changes.
These tests pin the planner to the anchor that does survive: the methods themselves.
"""

import unittest
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component, ScopeOperationAction, ScopeUpdateDecision
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.scope_ids import ROOT_SCOPE_ID
from diagram_analysis.exceptions import IncrementalClusteringError
from diagram_analysis.scope_plan import plan_scope_result_update
from static_analyzer.clustering import ClusterGroup, ClusterResult, ClusterScopeResult

FILE = "pkg/mod.py"
DELETE = ScopeOperationAction.DELETE_COMPONENT
CREATE = ScopeOperationAction.CREATE_COMPONENT
REPO = Path("/repo")


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


def actions(decision) -> dict[str, ScopeOperationAction]:
    """component_id (or the created name) -> the action planned for it."""
    return {op.component_id or op.name: op.action for op in decision.operations}


def plan_result(
    scope: AnalysisInsights,
    clusters: dict[int, set[str]],
    changed_members: set[str] | frozenset[str] = frozenset(),
    groups: list[ClusterGroup] | None = None,
    scope_id: str = ROOT_SCOPE_ID,
) -> ScopeUpdateDecision:
    if groups is None:
        groups = [
            ClusterGroup(group_id=str(cluster_id), cluster_ids=[cluster_id], previous_component_id=str(cluster_id))
            for cluster_id in clusters
        ]
    for group in groups:
        if not group.symbol_members_by_language:
            group.symbol_members_by_language = {
                "python": {
                    qualified_name
                    for cluster_id in group.cluster_ids
                    for qualified_name in clusters.get(cluster_id, set())
                }
            }
    result = ClusterScopeResult(
        scope_id=scope_id,
        leaf_clusters_by_language={"python": clustering(clusters)},
        groups=groups,
    )
    return plan_scope_result_update(scope, result, set(changed_members))


class TestPlanScopeResultUpdate(unittest.TestCase):
    def test_precomputed_scope_result_is_planned_without_regrouping(self):
        scope = AnalysisInsights(
            description="",
            components=[component("1", ["a.one"], ["1"])],
            components_relations=[],
        )
        result = ClusterScopeResult(
            scope_id=ROOT_SCOPE_ID,
            leaf_clusters_by_language={"python": clustering({1: {"a.one"}})},
            groups=[
                ClusterGroup(
                    group_id="1",
                    cluster_ids=[1],
                    symbol_members_by_language={"python": {"a.one"}},
                    previous_component_id="1",
                )
            ],
        )

        decision = plan_scope_result_update(scope, result, {"a.one"})

        self.assertEqual(actions(decision), {"1": ScopeOperationAction.UPDATE_COMPONENT})

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

        decision = plan_result(
            scope,
            renumbered,
            groups=[
                ClusterGroup(group_id="2.1", cluster_ids=[41], previous_component_id="2.1"),
                ClusterGroup(group_id="2.2", cluster_ids=[42], previous_component_id="2.2"),
                ClusterGroup(group_id="2.3", cluster_ids=[43], previous_component_id="2.3"),
            ],
            scope_id="2",
        )

        self.assertEqual(
            actions(decision),
            {
                "2.1": ScopeOperationAction.UPDATE_COMPONENT,
                "2.2": ScopeOperationAction.UPDATE_COMPONENT,
                "2.3": ScopeOperationAction.UPDATE_COMPONENT,
            },
        )

    def test_a_new_group_is_created_under_its_rule_name_unique_among_siblings(self):
        existing = component("1", ["a.one"], ["1"])
        existing.name = "Ingestion"
        scope = AnalysisInsights(description="", components=[existing], components_relations=[])
        groups = [
            ClusterGroup(group_id="1", cluster_ids=[1], previous_component_id="1"),
            ClusterGroup(group_id="2", name="Ingestion", cluster_ids=[2]),
            ClusterGroup(group_id="3", cluster_ids=[3]),
        ]

        decision = plan_result(scope, {1: {"a.one"}, 2: {"b.two"}, 3: {"c.three"}}, groups=groups)

        created = {
            op.component_id: op.name for op in decision.operations if op.action == ScopeOperationAction.CREATE_COMPONENT
        }
        self.assertEqual(created, {"2": "Ingestion 2", "3": "Component 3"})

    def test_a_retired_sibling_releases_its_name_to_a_new_group(self):
        retired = component("1", ["a.one"], ["1"])
        retired.name = "Storage"
        scope = AnalysisInsights(description="", components=[retired], components_relations=[])
        groups = [ClusterGroup(group_id="2", name="Storage", cluster_ids=[2])]

        decision = plan_result(scope, {2: {"b.two"}}, groups=groups)

        created = {op.component_id: op.name for op in decision.operations if op.action == CREATE}
        self.assertEqual(created, {"2": "Storage"})
        self.assertEqual(actions(decision)["1"], DELETE)

    def test_an_untouched_scope_plans_nothing_at_all(self):
        # An operation is not free: update_scope puts its target in refresh_ids, which
        # reruns the LLM relation analysis for the entire scope. A scope that did not
        # move must therefore produce zero operations, not a no-op update per component.
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

        decision = plan_result(scope, clusters)

        self.assertEqual(decision.operations, [])

    def test_a_body_edit_refreshes_only_the_component_that_owns_it(self):
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

        decision = plan_result(scope, clusters, {"b.one"})

        self.assertEqual(actions(decision), {"2": ScopeOperationAction.UPDATE_COMPONENT})

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

        decision = plan_result(scope, clusters)

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

        decision = plan_result(
            scope,
            clusters,
            groups=[
                ClusterGroup(group_id="1", cluster_ids=[1, 4], previous_component_id="1"),
                ClusterGroup(group_id="2", cluster_ids=[2], previous_component_id="2"),
                ClusterGroup(group_id="3", cluster_ids=[3], previous_component_id="3"),
            ],
        )

        # Only the absorbing component is touched; its siblings stay out of the plan.
        self.assertNotIn(ScopeOperationAction.CREATE_COMPONENT, {op.action for op in decision.operations})
        self.assertEqual(len(decision.operations), 1)
        absorbed = decision.operations[0]
        self.assertEqual(absorbed.action, ScopeOperationAction.UPDATE_COMPONENT)
        self.assertIn(4, {ref.cluster_id for ref in absorbed.cluster_refs})

    def test_every_planned_ref_is_tagged_with_its_language(self):
        clusters = {1: {"a.one"}, 2: {"b.one"}, 3: {"c.one"}}
        scope = AnalysisInsights(
            description="",
            components=[component(str(i), [q], [str(i)]) for i, q in enumerate(["a.one", "b.one", "c.one"], start=1)],
            components_relations=[],
        )

        decision = plan_result(scope, clusters, {"b.one"})

        for op in decision.operations:
            for ref in op.cluster_refs:
                self.assertEqual(ref.language, "python")
                self.assertEqual(ref.scope_id, ROOT_SCOPE_ID)

    def test_a_cluster_owner_with_no_methods_is_not_replaced(self):
        # A data-only cluster has no methods to speak for it, but the component owning it is
        # deliberately protected from pruning. Ownership must fall back to source_cluster_ids
        # or the planner deletes a stable leaf and creates a duplicate of it.
        clusters = {1: {"a.one"}, 2: {"b.one"}, 3: {"data.CONSTANT"}}
        methodless = Component(
            name="Cdata", description="", key_entities=[], component_id="3", source_cluster_ids=["3"], file_methods=[]
        )
        scope = AnalysisInsights(
            description="",
            components=[component("1", ["a.one"], ["1"]), component("2", ["b.one"], ["2"]), methodless],
            components_relations=[],
        )

        decision = plan_result(scope, clusters)

        self.assertNotIn("3", [op.component_id for op in decision.operations if op.action == DELETE])
        self.assertNotIn(ScopeOperationAction.CREATE_COMPONENT, {op.action for op in decision.operations})

    def test_a_scope_that_lost_every_cluster_deletes_its_components(self):
        scope = AnalysisInsights(
            description="",
            components=[
                Component(name="C1", description="", key_entities=[], component_id="1", source_cluster_ids=["1"]),
                Component(name="C2", description="", key_entities=[], component_id="2", source_cluster_ids=["2"]),
            ],
            components_relations=[],
        )

        decision = plan_result(scope, {})

        self.assertEqual(actions(decision), {"1": DELETE, "2": DELETE})

    def test_no_clusters_but_live_methods_fails_loud(self):
        # The subgraph builder guarantees a cluster per live method, so empty clusters with
        # live methods means the clustering could not represent the code -- fail, don't no-op.
        scope = AnalysisInsights(description="", components=[component("1", ["a.one"], ["1"])], components_relations=[])

        with self.assertRaises(IncrementalClusteringError):
            plan_result(scope, {})

    def test_an_empty_scope_plans_nothing(self):
        scope = AnalysisInsights(description="", components=[], components_relations=[])

        decision = plan_result(scope, {})

        self.assertEqual(decision.operations, [])


if __name__ == "__main__":
    unittest.main()
