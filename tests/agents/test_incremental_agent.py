"""Tests for the deterministic stitching/repopulation helpers in
``agents.incremental_agent``. The LLM-call shape (``IncrementalAgent.run``)
is exercised end-to-end in the diagram_generator tests with a mocked LLM."""

import unittest
from unittest.mock import MagicMock

from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    ClustersComponent,
    Component,
    FileMethodGroup,
    MethodEntry,
    Relation,
)
from agents.incremental_agent import (
    _format_existing_components,
    _pick_file_for_qname,
    prune_empty_components,
    repopulate_touched_scopes,
    stitch_delta,
)
from diagram_analysis.cluster_delta import ClusterDelta, LanguageDelta
from static_analyzer.graph import ClusterResult


def _component(name: str, component_id: str, source_cluster_ids: list[int] | None = None) -> Component:
    return Component(
        name=name,
        description=f"{name} description",
        key_entities=[],
        source_group_names=[name.lower()],
        source_cluster_ids=source_cluster_ids or [],
        component_id=component_id,
    )


def _component_with_method(name: str, component_id: str) -> Component:
    component = _component(name, component_id)
    component.file_methods = [
        FileMethodGroup(
            file_path=f"{component_id}.py",
            methods=[
                MethodEntry(
                    qualified_name=f"{component_id}.method",
                    start_line=1,
                    end_line=2,
                    node_type="FUNCTION",
                )
            ],
        )
    ]
    return component


def _empty_delta() -> ClusterDelta:
    return ClusterDelta(by_language={"python": LanguageDelta(language="python", cluster_results=ClusterResult())})


def _delta(
    new: set[int] | None = None,
    changed: set[int] | None = None,
    dropped: set[int] | None = None,
    remap: dict[int, int] | None = None,
) -> ClusterDelta:
    return ClusterDelta(
        by_language={
            "python": LanguageDelta(
                language="python",
                cluster_results=ClusterResult(),
                new_cluster_ids=new or set(),
                changed_cluster_ids=changed or set(),
                dropped_cluster_ids=dropped or set(),
                cluster_id_remap=remap or {},
            )
        }
    )


class TestStitchDelta(unittest.TestCase):
    def test_existing_component_absorbs_delta_clusters_and_redetails(self) -> None:
        comp = _component("Static Analyzer", "1", source_cluster_ids=[1, 2])
        root = AnalysisInsights(
            description="root",
            components=[comp],
            components_relations=[],
        )
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="Static Analyzer",
                    cluster_ids=[3],
                    description=comp.description,  # LLM reused verbatim
                    existing_component_id="1",
                )
            ]
        )

        plan = stitch_delta(root, {}, delta_ca, _empty_delta())

        self.assertIn("1", plan.refresh_ids)
        self.assertEqual(plan.expand_ids, set())
        self.assertEqual(comp.source_cluster_ids, [1, 2, 3])

    def test_duplicate_cluster_routes_keep_first_owner_only(self) -> None:
        first = _component("Scanner", "1", source_cluster_ids=[])
        second = _component("Cache", "2", source_cluster_ids=[])
        third = _component("Renderer", "3", source_cluster_ids=[])
        root = AnalysisInsights(description="root", components=[first, second, third], components_relations=[])
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="Scanner",
                    cluster_ids=[7],
                    description="scanner update",
                    existing_component_id="1",
                ),
                ClustersComponent(
                    name="Cache",
                    cluster_ids=[7],
                    description="duplicate cache claim",
                    existing_component_id="2",
                ),
                ClustersComponent(
                    name="Renderer",
                    cluster_ids=[7],
                    description="duplicate renderer claim",
                    existing_component_id="3",
                ),
            ]
        )

        plan = stitch_delta(root, {}, delta_ca, _empty_delta())

        self.assertEqual(first.source_cluster_ids, [7])
        self.assertEqual(second.source_cluster_ids, [])
        self.assertEqual(third.source_cluster_ids, [])
        self.assertEqual(plan.refresh_ids, {"1"})
        self.assertEqual(plan.expand_ids, set())

    def test_partially_duplicate_cluster_routes_keep_unique_remainder(self) -> None:
        first = _component("Scanner", "1", source_cluster_ids=[])
        second = _component("Language", "2", source_cluster_ids=[])
        root = AnalysisInsights(description="root", components=[first, second], components_relations=[])
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="Scanner",
                    cluster_ids=[7, 8],
                    description="scanner update",
                    existing_component_id="1",
                ),
                ClustersComponent(
                    name="Language",
                    cluster_ids=[8, 9],
                    description="language update",
                    existing_component_id="2",
                ),
            ]
        )

        plan = stitch_delta(root, {}, delta_ca, _empty_delta())

        self.assertEqual(first.source_cluster_ids, [7, 8])
        self.assertEqual(second.source_cluster_ids, [9])
        self.assertEqual(plan.refresh_ids, {"1", "2"})
        self.assertEqual(plan.expand_ids, set())

    def test_brand_new_component_attached_under_parent_id(self) -> None:
        parent = _component("Diagram Generator", "1", source_cluster_ids=[1])
        root = AnalysisInsights(description="root", components=[parent], components_relations=[])
        sub_analyses: dict[str, AnalysisInsights] = {}
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="Brand New Subsystem",
                    cluster_ids=[42],
                    description="freshly seen cluster",
                    parent_id="1",
                )
            ]
        )

        plan = stitch_delta(root, sub_analyses, delta_ca, _empty_delta())

        # Parent "1" was a leaf with no sub_analyses scope; stitch_delta creates one.
        self.assertEqual(len(root.components), 1)
        self.assertIn("1", sub_analyses)
        self.assertEqual(len(sub_analyses["1"].components), 1)
        new_component = sub_analyses["1"].components[0]
        self.assertEqual(new_component.name, "Brand New Subsystem")
        self.assertTrue(new_component.component_id)
        self.assertIn(new_component.component_id, plan.refresh_ids)
        self.assertIn(new_component.component_id, plan.expand_ids)
        self.assertEqual(new_component.source_cluster_ids, [42])

    def test_existing_component_skips_redetail_when_redetail_needed_false(self) -> None:
        """Cosmetic deltas preserve prose and component cluster ownership."""
        comp = _component("Static Analyzer", "1", source_cluster_ids=[1, 2])
        root = AnalysisInsights(description="root", components=[comp], components_relations=[])
        original_description = comp.description
        original_name = comp.name
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="Renamed By LLM",
                    cluster_ids=[3],
                    description="cosmetic",
                    existing_component_id="1",
                    redetail_needed=False,
                )
            ]
        )

        plan = stitch_delta(root, {}, delta_ca, _empty_delta())

        self.assertEqual(plan.refresh_ids, set())
        self.assertEqual(plan.expand_ids, set())
        self.assertEqual(comp.source_cluster_ids, [1, 2])
        # redetail_needed=False -> existing name/description preserved verbatim.
        self.assertEqual(comp.name, original_name)
        self.assertEqual(comp.description, original_description)

    def test_noop_route_does_not_propagate_delta_clusters_to_ancestors(self) -> None:
        top = _component("Top", "1", source_cluster_ids=[1])
        leaf = _component("Leaf", "1.1", source_cluster_ids=[2])
        root = AnalysisInsights(description="root", components=[top], components_relations=[])
        sub_analyses = {
            "1": AnalysisInsights(description="", components=[leaf], components_relations=[]),
        }
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="Leaf",
                    cluster_ids=[99],
                    description="cosmetic",
                    existing_component_id="1.1",
                    redetail_needed=False,
                )
            ]
        )

        plan = stitch_delta(root, sub_analyses, delta_ca, _empty_delta())

        self.assertEqual(leaf.source_cluster_ids, [2])
        self.assertEqual(top.source_cluster_ids, [1])
        self.assertEqual(plan.refresh_ids, set())
        self.assertEqual(plan.expand_ids, set())

    def test_existing_component_description_updated_when_redetail_needed_true(self) -> None:
        comp = _component("Static Analyzer", "1", source_cluster_ids=[1, 2])
        root = AnalysisInsights(description="root", components=[comp], components_relations=[])
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="Static Analyzer & Cluster Engine",
                    cluster_ids=[3],
                    description="now also performs Leiden clustering",
                    existing_component_id="1",
                    redetail_needed=True,
                )
            ]
        )

        stitch_delta(root, {}, delta_ca, _empty_delta())

        self.assertEqual(comp.name, "Static Analyzer & Cluster Engine")
        self.assertEqual(comp.description, "now also performs Leiden clustering")

    def test_existing_component_name_update_skipped_when_cc_name_empty(self) -> None:
        """Empty cc.name (LLM signalling reuse) must not blank out the existing name."""
        comp = _component("Static Analyzer", "1", source_cluster_ids=[1])
        root = AnalysisInsights(description="root", components=[comp], components_relations=[])
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="",
                    cluster_ids=[2],
                    description="",
                    existing_component_id="1",
                    redetail_needed=True,
                )
            ]
        )

        stitch_delta(root, {}, delta_ca, _empty_delta())

        self.assertEqual(comp.name, "Static Analyzer")
        self.assertEqual(comp.description, "Static Analyzer description")

    def test_brand_new_component_redetails_regardless_of_flag(self) -> None:
        """redetail_needed is meaningful only on existing-component routes; new ones always redetail."""
        parent = _component("Diagram Generator", "1", source_cluster_ids=[1])
        root = AnalysisInsights(description="root", components=[parent], components_relations=[])
        sub_analyses: dict[str, AnalysisInsights] = {}
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="Brand New Subsystem",
                    cluster_ids=[42],
                    description="freshly seen cluster",
                    parent_id="1",
                    redetail_needed=False,  # ignored for new components
                )
            ]
        )

        plan = stitch_delta(root, sub_analyses, delta_ca, _empty_delta())

        new_component = sub_analyses["1"].components[0]
        self.assertIn(new_component.component_id, plan.refresh_ids)
        self.assertIn(new_component.component_id, plan.expand_ids)

    def test_deterministic_remap_redetails_regardless_of_flag(self) -> None:
        """Step-1 deterministic remap/drop has no LLM signal to gate on; the cid is always redetailed."""
        comp = _component("X", "1", source_cluster_ids=[1, 2])
        root = AnalysisInsights(description="root", components=[comp], components_relations=[])
        # No cluster_components entry — the remap path runs in step 1 only.
        plan = stitch_delta(root, {}, ClusterAnalysis(cluster_components=[]), _delta(remap={1: 10}))

        self.assertEqual(plan.refresh_ids, {"1"})
        self.assertEqual(plan.expand_ids, set())

    def test_dropped_clusters_are_pruned_from_existing_components(self) -> None:
        comp = _component("X", "1", source_cluster_ids=[1, 2, 3])
        root = AnalysisInsights(description="root", components=[comp], components_relations=[])
        delta = _delta(dropped={2})

        plan = stitch_delta(root, {}, ClusterAnalysis(cluster_components=[]), delta)

        self.assertEqual(comp.source_cluster_ids, [1, 3])
        self.assertEqual(plan.refresh_ids, {"1"})
        self.assertEqual(plan.expand_ids, set())

    def test_cluster_id_remap_rewrites_existing_component_ids(self) -> None:
        comp = _component("X", "1", source_cluster_ids=[1, 2])
        root = AnalysisInsights(description="root", components=[comp], components_relations=[])
        delta = _delta(remap={1: 10, 2: 2})  # remap one, leave the other identity

        plan = stitch_delta(root, {}, ClusterAnalysis(cluster_components=[]), delta)

        self.assertEqual(comp.source_cluster_ids, [2, 10])
        self.assertEqual(plan.refresh_ids, {"1"})
        self.assertEqual(plan.expand_ids, set())

    def test_unchanged_component_is_not_redetailed(self) -> None:
        comp = _component("X", "1", source_cluster_ids=[5])
        root = AnalysisInsights(description="root", components=[comp], components_relations=[])

        plan = stitch_delta(root, {}, ClusterAnalysis(cluster_components=[]), _empty_delta())

        self.assertEqual(plan.refresh_ids, set())
        self.assertEqual(plan.expand_ids, set())
        self.assertEqual(comp.source_cluster_ids, [5])

    def test_routes_by_id_even_when_name_differs(self) -> None:
        """LLM-renamed component routed by id MUST update the existing component, not fork."""
        comp = _component("Authentication", "1.3", source_cluster_ids=[1])
        root = AnalysisInsights(description="root", components=[comp], components_relations=[])
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="Auth Service",  # different name; id is what counts
                    cluster_ids=[2],
                    description="renamed for clarity",
                    existing_component_id="1.3",
                )
            ]
        )

        stitch_delta(root, {}, delta_ca, _empty_delta())

        self.assertEqual(len(root.components), 1)
        self.assertEqual(comp.source_cluster_ids, [1, 2])

    def test_creates_new_component_when_existing_id_is_null(self) -> None:
        """Identity is by id. A null existing_component_id forks a new component
        even when the name collides with an existing one."""
        comp = _component("Authentication", "1.3", source_cluster_ids=[1])
        root = AnalysisInsights(description="root", components=[comp], components_relations=[])
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="Authentication",  # matches existing name
                    cluster_ids=[2],
                    description="brand-new component that happens to share the name",
                    existing_component_id=None,
                    parent_id=None,
                )
            ]
        )

        stitch_delta(root, {}, delta_ca, _empty_delta())

        self.assertEqual(len(root.components), 2)
        original = next(c for c in root.components if c.component_id == "1.3")
        new_one = next(c for c in root.components if c.component_id != "1.3")
        self.assertEqual(original.source_cluster_ids, [1])
        self.assertEqual(new_one.source_cluster_ids, [2])
        self.assertTrue(new_one.component_id, "new component should have an assigned id")

    def test_hallucinated_existing_component_id_is_treated_as_new(self) -> None:
        """If an unknown existing_component_id slips past the validator, the
        stitcher must not crash and must not silently lose the cluster_ids."""
        comp = _component("Existing", "1", source_cluster_ids=[1])
        root = AnalysisInsights(description="root", components=[comp], components_relations=[])
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="Hallucinated Routing",
                    cluster_ids=[42],
                    description="LLM made up an id",
                    existing_component_id="9.99",  # does not exist
                    parent_id=None,
                )
            ]
        )

        stitch_delta(root, {}, delta_ca, _empty_delta())

        self.assertEqual(len(root.components), 2)
        self.assertEqual(comp.source_cluster_ids, [1])  # untouched
        new_one = next(c for c in root.components if c.name == "Hallucinated Routing")
        self.assertEqual(new_one.source_cluster_ids, [42])

    def test_replaying_same_delta_is_idempotent(self) -> None:
        """Replay safety: if save_analysis succeeds but the cluster_cache seed
        crashes, the next run re-applies the same delta. Second application
        must not duplicate components or re-mutate cluster ids."""
        comp = _component("Static Analyzer", "1", source_cluster_ids=[1, 2])
        root = AnalysisInsights(description="root", components=[comp], components_relations=[])
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="Static Analyzer",
                    cluster_ids=[3],
                    description="d",
                    existing_component_id="1",
                )
            ]
        )

        first = stitch_delta(root, {}, delta_ca, _empty_delta())
        snapshot_components = [(c.component_id, c.name, list(c.source_cluster_ids)) for c in root.components]

        second = stitch_delta(root, {}, delta_ca, _empty_delta())

        self.assertEqual(first.refresh_ids, {"1"})
        self.assertEqual(first.expand_ids, set())
        self.assertEqual(second.refresh_ids, set())
        self.assertEqual(second.expand_ids, set())
        self.assertEqual(
            [(c.component_id, c.name, list(c.source_cluster_ids)) for c in root.components],
            snapshot_components,
        )

    def test_replaying_delta_with_dropped_clusters_is_idempotent(self) -> None:
        comp = _component("X", "1", source_cluster_ids=[1, 2, 3])
        root = AnalysisInsights(description="root", components=[comp], components_relations=[])
        delta = _delta(dropped={2})

        first = stitch_delta(root, {}, ClusterAnalysis(cluster_components=[]), delta)
        snapshot = list(comp.source_cluster_ids)

        second = stitch_delta(root, {}, ClusterAnalysis(cluster_components=[]), delta)

        self.assertEqual(first.refresh_ids, {"1"})
        self.assertEqual(first.expand_ids, set())
        self.assertEqual(second.refresh_ids, set())
        self.assertEqual(second.expand_ids, set())
        self.assertEqual(comp.source_cluster_ids, snapshot)

    def test_update_keeps_leaf_clusters_out_of_ancestors(self) -> None:
        """Routing into a leaf preserves ancestor component boundaries."""
        top = _component("Top", "1", source_cluster_ids=[1])
        mid = _component("Mid", "1.1", source_cluster_ids=[2])
        leaf = _component("Leaf", "1.1.1", source_cluster_ids=[3])
        root = AnalysisInsights(description="root", components=[top], components_relations=[])
        sub_analyses = {
            "1": AnalysisInsights(description="", components=[mid], components_relations=[]),
            "1.1": AnalysisInsights(description="", components=[leaf], components_relations=[]),
        }
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="Leaf",
                    cluster_ids=[99],
                    description="d",
                    existing_component_id="1.1.1",
                )
            ]
        )

        plan = stitch_delta(root, sub_analyses, delta_ca, _empty_delta())

        self.assertEqual(leaf.source_cluster_ids, [3, 99])
        self.assertEqual(mid.source_cluster_ids, [2])
        self.assertEqual(top.source_cluster_ids, [1])
        self.assertIn("1.1.1", plan.refresh_ids)
        self.assertNotIn("1.1", plan.refresh_ids)
        self.assertNotIn("1", plan.refresh_ids)
        self.assertEqual(plan.expand_ids, set())

    def test_qualified_detail_cluster_id_does_not_match_global_changed_cluster(self) -> None:
        top = _component("Top", "1", source_cluster_ids=[1])
        child = _component("Child", "1.1", source_cluster_ids=["1.7"])
        root = AnalysisInsights(description="root", components=[top], components_relations=[])
        sub_analyses = {
            "1": AnalysisInsights(description="", components=[child], components_relations=[]),
        }

        plan = stitch_delta(root, sub_analyses, ClusterAnalysis(cluster_components=[]), _delta(changed={7}))

        self.assertEqual(child.source_cluster_ids, ["1.7"])
        self.assertEqual(plan.refresh_ids, set())
        self.assertEqual(plan.expand_ids, set())

    def test_update_skips_propagation_when_ancestors_already_carry_ids(self) -> None:
        """Idempotency: a second run of the same delta is a no-op for ancestors."""
        top = _component("Top", "1", source_cluster_ids=[1, 99])
        mid = _component("Mid", "1.1", source_cluster_ids=[2, 99])
        leaf = _component("Leaf", "1.1.1", source_cluster_ids=[3, 99])
        root = AnalysisInsights(description="root", components=[top], components_relations=[])
        sub_analyses = {
            "1": AnalysisInsights(description="", components=[mid], components_relations=[]),
            "1.1": AnalysisInsights(description="", components=[leaf], components_relations=[]),
        }
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="Leaf",
                    cluster_ids=[99],
                    description="d",
                    existing_component_id="1.1.1",
                )
            ]
        )

        plan = stitch_delta(root, sub_analyses, delta_ca, _empty_delta())

        self.assertEqual(top.source_cluster_ids, [1, 99])
        self.assertEqual(mid.source_cluster_ids, [2, 99])
        self.assertEqual(leaf.source_cluster_ids, [3, 99])
        # Leaf's set didn't change; ancestors didn't change. No refresh needed.
        self.assertEqual(plan.refresh_ids, set())
        self.assertEqual(plan.expand_ids, set())

    def test_add_new_component_keeps_clusters_out_of_ancestors(self) -> None:
        """A brand-new child component does not mutate ancestor source clusters."""
        top = _component("Top", "1", source_cluster_ids=[1])
        mid = _component("Mid", "1.1", source_cluster_ids=[2])
        root = AnalysisInsights(description="root", components=[top], components_relations=[])
        sub_analyses: dict[str, AnalysisInsights] = {
            "1": AnalysisInsights(description="", components=[mid], components_relations=[]),
        }
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="Brand New",
                    cluster_ids=[42],
                    description="freshly seen cluster",
                    parent_id="1.1",
                )
            ]
        )

        plan = stitch_delta(root, sub_analyses, delta_ca, _empty_delta())

        self.assertEqual(top.source_cluster_ids, [1])
        self.assertEqual(mid.source_cluster_ids, [2])
        self.assertNotIn("1", plan.refresh_ids)
        self.assertNotIn("1.1", plan.refresh_ids)
        self.assertEqual({"1", "1.1"} & plan.expand_ids, set())
        # The new component should also be present and registered for redetail.
        new_component = sub_analyses["1.1"].components[0]
        self.assertEqual(new_component.name, "Brand New")
        self.assertIn(new_component.component_id, plan.refresh_ids)
        self.assertIn(new_component.component_id, plan.expand_ids)

    def test_add_top_level_component_does_not_propagate(self) -> None:
        """A new top-level component (parent_id=None) has no ancestors to update."""
        existing = _component("Existing", "1", source_cluster_ids=[1])
        root = AnalysisInsights(description="root", components=[existing], components_relations=[])
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="New Top",
                    cluster_ids=[99],
                    description="d",
                    parent_id=None,
                )
            ]
        )

        stitch_delta(root, {}, delta_ca, _empty_delta())

        # The existing top-level component is a sibling, not an ancestor.
        # Its source_cluster_ids must stay untouched.
        self.assertEqual(existing.source_cluster_ids, [1])


class TestFormatExistingComponents(unittest.TestCase):
    def test_renders_id_name_and_description_in_full(self) -> None:
        # Why no truncation: the cluster-string budget downstream
        # (``_build_cluster_string``) accounts for component-section overhead
        # when planning skip sets, so descriptions render in full and the
        # cluster body trims itself to fit.
        long_desc = "x" * 500
        comp = Component(
            name="Big",
            description=long_desc,
            key_entities=[],
            component_id="3",
        )
        analysis = AnalysisInsights(description="r", components=[comp], components_relations=[])

        rendered = _format_existing_components(analysis, {})

        self.assertIn("3", rendered)
        self.assertIn("Big", rendered)
        self.assertIn(long_desc, rendered)
        self.assertFalse(rendered.endswith("..."))

    def test_empty_baseline_message(self) -> None:
        empty = AnalysisInsights(description="r", components=[], components_relations=[])
        rendered = _format_existing_components(empty, {})
        self.assertIn("no existing components", rendered)


class TestPruneEmptyComponents(unittest.TestCase):
    def test_strips_relations_by_id_not_duplicate_name(self) -> None:
        discovery = _component_with_method("Discovery & Extraction Engine", "1")
        graph = _component_with_method("Graph Synthesis & Normalization", "2")
        removed_root = _component("Removed Root", "9")
        root = AnalysisInsights(
            description="root",
            components=[discovery, graph, removed_root],
            components_relations=[
                Relation(
                    relation="sends raw data to",
                    src_name=discovery.name,
                    dst_name=graph.name,
                    src_id="1",
                    dst_id="2",
                ),
                Relation(
                    relation="obsolete",
                    src_name=discovery.name,
                    dst_name=removed_root.name,
                    src_id="1",
                    dst_id="9",
                ),
            ],
        )
        sub_analyses = {
            "3.2": AnalysisInsights(
                description="sub",
                components=[_component("Graph Synthesis & Normalization", "3.2.4")],
                components_relations=[],
            )
        }

        removed_ids = prune_empty_components(root, sub_analyses)

        self.assertEqual(removed_ids, {"3.2.4", "9"})
        self.assertEqual([(r.src_id, r.dst_id) for r in root.components_relations], [("1", "2")])
        self.assertEqual([c.component_id for c in root.components], ["1", "2"])


class TestRepopulateTouchedScopes(unittest.TestCase):
    def test_only_runs_relation_pass_on_touched_scopes(self) -> None:
        # Per-component file_methods refresh now happens inline (not via the
        # mixin's scope-wide ``populate_file_methods``), so the only mixin
        # method we expect to be called is ``build_static_relations`` — and
        # only on the touched scope (the sub-analysis containing "2.1"),
        # never on the untouched root.
        root_comp = _component("Root", "1", source_cluster_ids=[1])
        sub_comp = _component("Sub", "2.1", source_cluster_ids=[2])
        root = AnalysisInsights(description="r", components=[root_comp], components_relations=[])
        sub = AnalysisInsights(description="s", components=[sub_comp], components_relations=[])
        sub_analyses = {"2": sub}

        helpers = MagicMock()
        helpers.static_analysis = MagicMock()
        helpers.static_analysis.get_cfg.return_value = MagicMock(nodes={})

        touched = repopulate_touched_scopes({"2.1"}, root, sub_analyses, {"python": ClusterResult()}, helpers)

        self.assertEqual(touched, {"2"})
        helpers.populate_file_methods.assert_not_called()
        helpers.build_static_relations.assert_called_once()

    def test_no_redetail_skips_all_helpers(self) -> None:
        root = AnalysisInsights(description="r", components=[], components_relations=[])
        helpers = MagicMock()
        helpers.static_analysis = MagicMock()
        helpers.static_analysis.get_cfg.return_value = MagicMock(nodes={})
        touched = repopulate_touched_scopes(set(), root, {}, {}, helpers)
        self.assertEqual(touched, set())
        helpers.populate_file_methods.assert_not_called()
        helpers.build_static_relations.assert_not_called()


class TestRefreshComponentFileMethodsPathNormalization(unittest.TestCase):
    """``_refresh_component_file_methods`` must normalize absolute snapshot-
    worktree paths before substring-matching against qnames. Without that
    normalization the absolute prefix poisons the match and every qname
    falls through to the alphabetical fallback, bucketing 100s of methods
    under whichever file sorts first (e.g. ``shared/incrementalTypes.ts``
    in test 04). Reproduces the test 04 misbucketing bug."""

    def test_absolute_paths_in_cluster_to_files_normalize_before_matching(self) -> None:
        from agents.agent_responses import MethodEntry
        from agents.incremental_agent import _refresh_component_file_methods
        from static_analyzer.graph import ClusterResult

        repo_dir = "/tmp/snapshot-worktree"
        # Mirror what static_analyzer's CFG produces under a snapshot worktree:
        # every ``cluster_to_files`` entry is an absolute path under repo_dir.
        cluster_to_files = {
            1: {
                f"{repo_dir}/shared/incrementalTypes.ts",
                f"{repo_dir}/webview-ui/src/components/HealthSummary.tsx",
            }
        }
        clusters = {
            1: {
                "shared.incrementalTypes.MethodChange",
                "webview-ui.src.components.HealthSummary.HealthSummaryProps",
            }
        }
        cluster_results = {"typescript": ClusterResult(clusters=clusters, cluster_to_files=cluster_to_files)}
        node_lookup = {
            qname: MethodEntry(qualified_name=qname, start_line=1, end_line=1, node_type="FUNCTION")
            for qname in clusters[1]
        }

        comp = _component("UI Bridge", "1", source_cluster_ids=[1])
        _refresh_component_file_methods(comp, cluster_results, node_lookup, repo_dir)

        by_file = {g.file_path: [m.qualified_name for m in g.methods] for g in comp.file_methods}
        self.assertIn("shared/incrementalTypes.ts", by_file)
        self.assertIn("webview-ui/src/components/HealthSummary.tsx", by_file)
        self.assertEqual(
            by_file["shared/incrementalTypes.ts"],
            ["shared.incrementalTypes.MethodChange"],
            "incrementalTypes qname must NOT pull in HealthSummary qnames via fallback",
        )
        self.assertEqual(
            by_file["webview-ui/src/components/HealthSummary.tsx"],
            ["webview-ui.src.components.HealthSummary.HealthSummaryProps"],
            "HealthSummary qname must bucket to its own file via substring match (post-normalization)",
        )

    def test_scoped_refresh_preserves_unchanged_files_and_ignores_cluster_drift(self) -> None:
        from agents.incremental_agent import _refresh_component_file_methods

        comp = _component("Boundary", "1", source_cluster_ids=[1])
        comp.file_methods = [
            FileMethodGroup(
                file_path="stable.py",
                methods=[MethodEntry(qualified_name="stable.fn", start_line=1, end_line=2, node_type="FUNCTION")],
            )
        ]
        clusters = {1: {"changed.fn", "unrelated.fn"}}
        cluster_to_files = {1: {"changed.py", "unrelated.py"}}
        node_lookup = {
            "changed.fn": MethodEntry(qualified_name="changed.fn", start_line=10, end_line=12, node_type="FUNCTION"),
            "unrelated.fn": MethodEntry(
                qualified_name="unrelated.fn", start_line=20, end_line=22, node_type="FUNCTION"
            ),
        }

        _refresh_component_file_methods(
            comp,
            {"python": ClusterResult(clusters=clusters, cluster_to_files=cluster_to_files)},
            node_lookup,
            refresh_files={"changed.py"},
        )

        by_file = {group.file_path: [method.qualified_name for method in group.methods] for group in comp.file_methods}
        self.assertEqual(set(by_file), {"stable.py", "changed.py"})
        self.assertEqual(by_file["stable.py"], ["stable.fn"])
        self.assertEqual(by_file["changed.py"], ["changed.fn"])


class TestIncrementalAgentToolkit(unittest.TestCase):
    """Constructor wiring: routing should never see the full ReAct toolkit.

    The agent shouldn't have ``read_packages`` / ``read_file`` / etc. attached;
    a ReAct loop with the full kit speculatively wanders for tens of rounds on
    a routing decision that needs at most a single targeted source read. Only
    ``read_source_reference`` is appropriate.
    """

    def test_only_read_source_reference_is_attached(self) -> None:
        from unittest.mock import patch
        from pathlib import Path

        from agents.incremental_agent import IncrementalAgent
        from static_analyzer.analysis_result import StaticAnalysisResults

        static_analysis = MagicMock(spec=StaticAnalysisResults)

        # The base ``CodeBoardingAgent.__init__`` builds a ReAct agent with the
        # full toolkit; ``IncrementalAgent.__init__`` then overrides ``self.agent``
        # by rebuilding it with the narrow tool set. Patch both create_agent
        # references and assert the override call carries the single tool.
        with (
            patch("agents.agent.create_agent") as mock_base_create,
            patch("agents.incremental_agent.create_agent") as mock_override_create,
        ):
            mock_base_create.return_value = MagicMock()
            mock_override_create.return_value = MagicMock()
            IncrementalAgent(
                repo_dir=Path("/tmp/fake-repo"),
                static_analysis=static_analysis,
                project_name="Test",
                meta_context=None,
                agent_llm=MagicMock(),
                parsing_llm=MagicMock(),
            )

        mock_override_create.assert_called_once()
        tools = mock_override_create.call_args.kwargs["tools"]
        # CodeReferenceReader is the BaseRepoTool subclass behind read_source_reference.
        self.assertEqual(len(tools), 1, f"expected 1 tool, got {len(tools)}: {[type(t).__name__ for t in tools]}")
        self.assertEqual(type(tools[0]).__name__, "CodeReferenceReader")


class TestPickFileForQname(unittest.TestCase):
    """Substring-match tie-break: longest dotted prefix wins.

    Why these matter: any other tie-break (alphabetical, set order) would
    randomly route ``foo.py`` and ``foo_test.py`` methods between the two,
    producing churn across runs.
    """

    def test_dotted_prefix_match_picks_correct_file(self) -> None:
        files = {"a/b/foo.py", "a/b/bar.py"}
        chosen = _pick_file_for_qname("a.b.foo.fn", files, qname_to_files={})
        self.assertEqual(chosen, "a/b/foo.py")

    def test_longer_dotted_match_wins_over_shorter(self) -> None:
        # ``a.b.foo`` is a proper prefix of ``a.b.foo_x``; both files match
        # the qname ``a.b.foo_x.run`` via substring (``a.b.foo`` is contained
        # in ``a.b.foo_x.run``). The longer dotted form must win.
        files = {"a/b/foo.py", "a/b/foo_x.py"}
        chosen = _pick_file_for_qname("a.b.foo_x.run", files, qname_to_files={})
        self.assertEqual(chosen, "a/b/foo_x.py")

    def test_no_match_falls_back_to_qname_to_files(self) -> None:
        # No file in files_for_cluster matches; qname_to_files knows the qname.
        chosen = _pick_file_for_qname(
            "totally.unrelated.fn",
            files_for_cluster={"x/y/z.py"},
            qname_to_files={"totally.unrelated.fn": {"some/other/file.py"}},
        )
        self.assertEqual(chosen, "some/other/file.py")

    def test_no_match_anywhere_falls_back_to_first_cluster_file(self) -> None:
        files = {"x/y/z.py", "a/b/c.py"}
        chosen = _pick_file_for_qname("totally.unrelated", files, qname_to_files={})
        # Sorted-first deterministic choice.
        self.assertEqual(chosen, "a/b/c.py")

    def test_empty_inputs_return_empty_string(self) -> None:
        self.assertEqual(_pick_file_for_qname("foo.bar", set(), qname_to_files={}), "")


if __name__ == "__main__":
    unittest.main()
