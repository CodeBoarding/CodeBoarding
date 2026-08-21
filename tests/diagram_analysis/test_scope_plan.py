"""The deterministic scope planner: what survives a re-clustering, and what may not.

A scope's leaf clusters are re-derived from its subgraph on every run, so their integer
ids are not an identity — they renumber whenever the code inside the scope changes.
These tests pin the planner to the anchor that does survive: the methods themselves.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

import networkx as nx

from agents.agent_responses import AnalysisInsights, Component, ScopeOperationAction
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.scope_ids import ROOT_SCOPE_ID
from diagram_analysis.exceptions import IncrementalClusteringError
from diagram_analysis.scope_plan import plan_scope_result_update, plan_scope_update, previous_ownership
from static_analyzer.clustering import ClusterGroup, ClusterResult, ClusterScopeResult

FILE = "pkg/mod.py"
DELETE = ScopeOperationAction.DELETE_COMPONENT
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

        owner, _member_owner = previous_ownership(
            scope, {"python": clustering({7: {"a.one", "a.two", "b.one"}})}, ROOT_SCOPE_ID, REPO
        )

        self.assertEqual(owner, {7: "1"})

    def test_a_cluster_of_entirely_new_methods_has_no_owner(self):
        scope = AnalysisInsights(description="", components=[component("1", ["a.one"], ["1"])], components_relations=[])

        owner, _member_owner = previous_ownership(
            scope, {"python": clustering({1: {"a.one"}, 2: {"fresh.thing"}})}, ROOT_SCOPE_ID, REPO
        )

        self.assertEqual(owner, {1: "1"})

    def test_an_even_split_resolves_to_the_lowest_component_id(self):
        # Whichever way it goes it must go the same way on every run, or a rerun
        # with no source change would hand the cluster to a different component.
        scope = AnalysisInsights(
            description="",
            components=[component("2", ["b.one"], ["2"]), component("1", ["a.one"], ["1"])],
            components_relations=[],
        )

        owner, _member_owner = previous_ownership(
            scope, {"python": clustering({5: {"a.one", "b.one"}})}, ROOT_SCOPE_ID, REPO
        )

        self.assertEqual(owner, {5: "1"})

    def test_a_qname_shared_across_languages_stays_with_its_own_language(self):
        # src/index.py and src/index.ts both yield the qname 'src.index.run'; each language's
        # cluster must be attributed to that language's component, not to whoever was seen last.
        def group(file_path: str, qname: str) -> FileMethodGroup:
            return FileMethodGroup(file_path=file_path, methods=[method(qname)])

        py = Component(
            name="Py",
            description="",
            key_entities=[],
            component_id="1",
            source_cluster_ids=["1"],
            file_methods=[group("src/index.py", "src.index.run")],
        )
        ts = Component(
            name="Ts",
            description="",
            key_entities=[],
            component_id="2",
            source_cluster_ids=["30"],
            file_methods=[group("src/index.ts", "src.index.run")],
        )
        scope = AnalysisInsights(description="", components=[py, ts], components_relations=[])
        cluster_results = {
            "python": ClusterResult(
                clusters={1: {"src.index.run"}},
                cluster_to_files={1: {"src/index.py"}},
                file_to_clusters={"src/index.py": {1}},
                strategy="t",
            ),
            "typescript": ClusterResult(
                clusters={30: {"src.index.run"}},
                cluster_to_files={30: {"src/index.ts"}},
                file_to_clusters={"src/index.ts": {30}},
                strategy="t",
            ),
        }

        owner, member_owner = previous_ownership(scope, cluster_results, ROOT_SCOPE_ID, REPO)

        self.assertEqual(owner, {1: "1", 30: "2"})
        self.assertEqual(member_owner, {"python": {"src.index.run": "1"}, "typescript": {"src.index.run": "2"}})

    def test_anchors_by_method_when_cfg_paths_are_absolute(self):
        # The real production mismatch: cluster_to_files carries the CFG's absolute paths
        # while file_methods is repo-relative. Without normalizing both sides the file
        # filter excludes every method and anchoring silently falls back to cluster ids.
        # The cluster has RENUMBERED (id 99, not the component's stored "1"), so the two
        # anchors diverge: method-anchoring still finds the owner, the id fallback cannot.
        py = Component(
            name="Py",
            description="",
            key_entities=[],
            component_id="1",
            source_cluster_ids=["1"],
            file_methods=[FileMethodGroup(file_path="src/index.py", methods=[method("src.index.run")])],
        )
        scope = AnalysisInsights(description="", components=[py], components_relations=[])
        cluster_results = {
            "python": ClusterResult(
                clusters={99: {"src.index.run"}},
                cluster_to_files={99: {str(REPO / "src/index.py")}},  # absolute, as the CFG emits
                file_to_clusters={str(REPO / "src/index.py"): {99}},
                strategy="t",
            )
        }

        owner, _member_owner = previous_ownership(scope, cluster_results, ROOT_SCOPE_ID, REPO)

        # Method-anchored: renumbered cluster 99 still resolves to component 1. The id
        # fallback would leave it unowned, so an empty result means anchoring broke.
        self.assertEqual(owner, {99: "1"})


class TestPlanScopeUpdate(unittest.TestCase):
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

        with patch(
            "diagram_analysis.scope_plan.GroupingService.anchored_group",
            side_effect=AssertionError("precomputed results must not be regrouped"),
        ):
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

        decision = plan_scope_update(
            "2", scope, {"python": clustering(renumbered)}, {"python": cfg_for(renumbered)}, set(), REPO
        )

        self.assertEqual(
            actions(decision),
            {
                "2.1": ScopeOperationAction.UPDATE_COMPONENT,
                "2.2": ScopeOperationAction.UPDATE_COMPONENT,
                "2.3": ScopeOperationAction.UPDATE_COMPONENT,
            },
        )

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

        decision = plan_scope_update(
            ROOT_SCOPE_ID, scope, {"python": clustering(clusters)}, {"python": cfg_for(clusters)}, set(), REPO
        )

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

        decision = plan_scope_update(
            ROOT_SCOPE_ID, scope, {"python": clustering(clusters)}, {"python": cfg_for(clusters)}, {"b.one"}, REPO
        )

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

        decision = plan_scope_update(
            ROOT_SCOPE_ID, scope, {"python": clustering(clusters)}, {"python": cfg_for(clusters)}, set(), REPO
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
            ROOT_SCOPE_ID, scope, {"python": clustering(clusters)}, {"python": cfg_for(clusters)}, set(), REPO
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

        decision = plan_scope_update(
            ROOT_SCOPE_ID, scope, {"python": clustering(clusters)}, {"python": cfg_for(clusters)}, set(), REPO
        )

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

        decision = plan_scope_update(
            ROOT_SCOPE_ID, scope, {"python": clustering(clusters)}, {"python": cfg_for(clusters)}, set(), REPO
        )

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

        decision = plan_scope_update(ROOT_SCOPE_ID, scope, {"python": clustering({})}, {}, set(), REPO)

        self.assertEqual(actions(decision), {"1": DELETE, "2": DELETE})

    def test_no_clusters_but_live_methods_fails_loud(self):
        # The subgraph builder guarantees a cluster per live method, so empty clusters with
        # live methods means the clustering could not represent the code -- fail, don't no-op.
        scope = AnalysisInsights(description="", components=[component("1", ["a.one"], ["1"])], components_relations=[])

        with self.assertRaises(IncrementalClusteringError):
            plan_scope_update(ROOT_SCOPE_ID, scope, {"python": clustering({})}, {}, set(), REPO)

    def test_an_empty_scope_plans_nothing(self):
        scope = AnalysisInsights(description="", components=[], components_relations=[])

        decision = plan_scope_update(ROOT_SCOPE_ID, scope, {"python": clustering({})}, {}, set(), REPO)

        self.assertEqual(decision.operations, [])


if __name__ == "__main__":
    unittest.main()
