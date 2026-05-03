"""Tests for the deterministic stitching/repopulation helpers in
``agents.incremental_agent``. The LLM-call shape (``IncrementalAgent.step_group_delta``)
is exercised end-to-end in the diagram_generator tests with a mocked LLM."""

import unittest
from unittest.mock import MagicMock

from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    ClustersComponent,
    Component,
)
from agents.incremental_agent import (
    _format_existing_components,
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
                    description="ignored for existing component",
                )
            ]
        )

        redetail = stitch_delta(root, {}, delta_ca, _empty_delta())

        self.assertIn("1", redetail)
        self.assertEqual(comp.source_cluster_ids, [1, 2, 3])

    def test_brand_new_component_attached_under_parent_id(self) -> None:
        parent = _component("Diagram Generator", "1", source_cluster_ids=[1])
        root = AnalysisInsights(description="root", components=[parent], components_relations=[])
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

        redetail = stitch_delta(root, {}, delta_ca, _empty_delta())

        self.assertEqual(len(root.components), 2)
        new_component = next(c for c in root.components if c.name == "Brand New Subsystem")
        # New component got an ID via assign_component_ids(only_new=True) and is in redetail.
        self.assertTrue(new_component.component_id)
        self.assertIn(new_component.component_id, redetail)
        self.assertEqual(new_component.source_cluster_ids, [42])

    def test_dropped_clusters_are_pruned_from_existing_components(self) -> None:
        comp = _component("X", "1", source_cluster_ids=[1, 2, 3])
        root = AnalysisInsights(description="root", components=[comp], components_relations=[])
        delta = _delta(dropped={2})

        redetail = stitch_delta(root, {}, ClusterAnalysis(cluster_components=[]), delta)

        self.assertEqual(comp.source_cluster_ids, [1, 3])
        self.assertEqual(redetail, {"1"})

    def test_cluster_id_remap_rewrites_existing_component_ids(self) -> None:
        comp = _component("X", "1", source_cluster_ids=[1, 2])
        root = AnalysisInsights(description="root", components=[comp], components_relations=[])
        delta = _delta(remap={1: 10, 2: 2})  # remap one, leave the other identity

        redetail = stitch_delta(root, {}, ClusterAnalysis(cluster_components=[]), delta)

        self.assertEqual(comp.source_cluster_ids, [2, 10])
        self.assertEqual(redetail, {"1"})

    def test_unchanged_component_is_not_redetailed(self) -> None:
        comp = _component("X", "1", source_cluster_ids=[5])
        root = AnalysisInsights(description="root", components=[comp], components_relations=[])

        redetail = stitch_delta(root, {}, ClusterAnalysis(cluster_components=[]), _empty_delta())

        self.assertEqual(redetail, set())
        self.assertEqual(comp.source_cluster_ids, [5])

    def test_existing_name_collision_silently_merges(self) -> None:
        """LLM unintentionally reusing a name should NOT create a duplicate component."""
        comp = _component("Existing", "1", source_cluster_ids=[1])
        root = AnalysisInsights(description="root", components=[comp], components_relations=[])
        delta_ca = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(
                    name="EXISTING",  # case-insensitive collision
                    cluster_ids=[2],
                    description="should merge",
                    parent_id=None,
                )
            ]
        )

        stitch_delta(root, {}, delta_ca, _empty_delta())

        self.assertEqual(len(root.components), 1)
        self.assertEqual(comp.source_cluster_ids, [1, 2])


class TestFormatExistingComponents(unittest.TestCase):
    def test_renders_id_name_and_truncated_description(self) -> None:
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
        self.assertTrue(rendered.endswith("..."), f"expected truncation marker, got: {rendered!r}")

    def test_empty_baseline_message(self) -> None:
        empty = AnalysisInsights(description="r", components=[], components_relations=[])
        rendered = _format_existing_components(empty, {})
        self.assertIn("no existing components", rendered)


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

        with patch("agents.agent.create_agent") as mock_create_agent:
            mock_create_agent.return_value = MagicMock()
            IncrementalAgent(
                repo_dir=Path("/tmp/fake-repo"),
                static_analysis=static_analysis,
                project_name="Test",
                meta_context=None,
                agent_llm=MagicMock(),
                parsing_llm=MagicMock(),
            )

        mock_create_agent.assert_called_once()
        tools = mock_create_agent.call_args.kwargs["tools"]
        # CodeReferenceReader is the BaseRepoTool subclass behind read_source_reference.
        self.assertEqual(len(tools), 1, f"expected 1 tool, got {len(tools)}: {[type(t).__name__ for t in tools]}")
        self.assertEqual(type(tools[0]).__name__, "CodeReferenceReader")


if __name__ == "__main__":
    unittest.main()
