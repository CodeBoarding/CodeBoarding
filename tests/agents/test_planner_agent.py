"""
Tests for the deterministic planner_agent module.

The planner uses CFG structure (source_cluster_ids, assigned_files) and parent context
to determine which components should be expanded - no LLM calls.

Expansion Rules:
1. Has clusters → expand (CFG structure exists)
2. No clusters but has files AND parent had clusters → expand one level
3. No clusters AND parent had no clusters → leaf (stop)
"""

import unittest

from agents.planner_agent import should_expand_component, plan_analysis
from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileMethodGroup,
    SourceCodeReference,
)


class TestShouldExpandComponent(unittest.TestCase):
    """Tests for should_expand_component function."""

    def _make_component(
        self,
        name: str = "TestComponent",
        cluster_ids: list[int] | None = None,
        file_count: int = 5,
    ) -> Component:
        """Helper to create test components."""
        ref = SourceCodeReference(
            qualified_name=f"{name}.Class",
            reference_file=f"{name.lower()}.py",
            reference_start_line=1,
            reference_end_line=10,
        )
        return Component(
            name=name,
            description=f"Test component {name}",
            key_entities=[ref],
            source_cluster_ids=cluster_ids or [],
            file_methods=[FileMethodGroup(file_path=f"file{i}.py", methods=[]) for i in range(file_count)],
        )

    def test_expand_with_clusters(self):
        """Component with clusters should expand (CFG structure exists)."""
        component = self._make_component(cluster_ids=[1, 2, 3], file_count=5)
        self.assertTrue(should_expand_component(component))

    def test_expand_with_clusters_parent_irrelevant(self):
        """Component with clusters expands regardless of parent status."""
        component = self._make_component(cluster_ids=[1], file_count=1)
        # Parent had clusters
        self.assertTrue(should_expand_component(component, parent_had_clusters=True))
        # Parent had no clusters - still expands because component has clusters
        self.assertTrue(should_expand_component(component, parent_had_clusters=False))

    def test_expand_no_clusters_but_files_parent_had_clusters(self):
        """Component without clusters but with files expands if parent had clusters."""
        component = self._make_component(cluster_ids=[], file_count=3)
        self.assertTrue(should_expand_component(component, parent_had_clusters=True))

    def test_no_expand_no_clusters_parent_also_no_clusters(self):
        """Component without clusters is leaf if parent also had no clusters."""
        component = self._make_component(cluster_ids=[], file_count=10)
        self.assertFalse(should_expand_component(component, parent_had_clusters=False))

    def test_no_expand_no_content(self):
        """Component with no clusters and no files should not expand."""
        component = self._make_component(cluster_ids=[], file_count=0)
        self.assertFalse(should_expand_component(component, parent_had_clusters=True))
        self.assertFalse(should_expand_component(component, parent_had_clusters=False))

    def test_default_parent_had_clusters_is_true(self):
        """Default assumes parent had clusters (for top-level components)."""
        component = self._make_component(cluster_ids=[], file_count=5)
        # Without explicit parent_had_clusters, defaults to True
        self.assertTrue(should_expand_component(component))

    def test_single_file_component_expands_from_clustered_parent(self):
        """Single file component (like details_agent.py) expands if parent had clusters."""
        component = Component(
            name="DetailsAgent",
            description="Agent for detailed analysis",
            key_entities=[],
            source_cluster_ids=[],  # No clusters
            file_methods=[FileMethodGroup(file_path="agents/details_agent.py", methods=[])],  # Single file
        )
        # Parent (Agents component) had clusters → can expand to explain file internals
        self.assertTrue(should_expand_component(component, parent_had_clusters=True))

    def test_function_level_does_not_expand(self):
        """Sub-component of a file-level component should not expand further."""
        component = Component(
            name="run_method",
            description="Main run method of DetailsAgent",
            key_entities=[],
            source_cluster_ids=[],  # No clusters
            file_methods=[],  # No files (it's a method, not a file)
        )
        # Parent (DetailsAgent) had no clusters → we're at leaf level
        self.assertFalse(should_expand_component(component, parent_had_clusters=False))


class TestPlanAnalysis(unittest.TestCase):
    """Tests for plan_analysis function."""

    def _make_component(
        self,
        name: str,
        cluster_ids: list[int] | None = None,
        file_count: int = 5,
    ) -> Component:
        """Helper to create test components."""
        return Component(
            name=name,
            description=f"Test component {name}",
            key_entities=[],
            source_cluster_ids=cluster_ids or [],
            file_methods=[
                FileMethodGroup(file_path=f"{name.lower()}_file{i}.py", methods=[]) for i in range(file_count)
            ],
        )

    def test_plan_analysis_top_level_with_clusters(self):
        """Top-level components with clusters should be expandable."""
        comp1 = self._make_component("Comp1", cluster_ids=[1, 2], file_count=5)
        comp2 = self._make_component("Comp2", cluster_ids=[3, 4, 5], file_count=10)

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[comp1, comp2],
            components_relations=[],
        )

        # Top-level: parent_had_clusters defaults to True
        result = plan_analysis(analysis)

        self.assertEqual(len(result), 2)

    def test_plan_analysis_mixed_components(self):
        """Mix of clustered and file-only components from clustered parent."""
        clustered = self._make_component("Clustered", cluster_ids=[1, 2], file_count=5)
        file_only = self._make_component("FileOnly", cluster_ids=[], file_count=3)

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[clustered, file_only],
            components_relations=[],
        )

        # Parent had clusters → both should expand
        result = plan_analysis(analysis, parent_had_clusters=True)
        self.assertEqual(len(result), 2)

    def test_plan_analysis_file_only_from_non_clustered_parent(self):
        """File-only components from non-clustered parent should not expand."""
        file_only1 = self._make_component("FileOnly1", cluster_ids=[], file_count=3)
        file_only2 = self._make_component("FileOnly2", cluster_ids=[], file_count=5)

        analysis = AnalysisInsights(
            description="Sub-analysis of file-level component",
            components=[file_only1, file_only2],
            components_relations=[],
        )

        # Parent had no clusters → these are leaves
        result = plan_analysis(analysis, parent_had_clusters=False)
        self.assertEqual(len(result), 0)

    def test_plan_analysis_empty_components(self):
        """Empty component list should return empty result."""
        analysis = AnalysisInsights(
            description="Empty analysis",
            components=[],
            components_relations=[],
        )

        result = plan_analysis(analysis)
        self.assertEqual(len(result), 0)

    def test_hierarchical_expansion_scenario(self):
        """
        Test realistic hierarchical expansion:
        - Level 0: "Agents" component with clusters → expand
        - Level 1: "DetailsAgent" file-level (no clusters, 1 file) → expand (parent had clusters)
        - Level 2: "run_method" (no clusters, no files) → DON'T expand (parent had no clusters)
        """
        # Level 0: Top-level component with clusters
        agents_component = self._make_component("Agents", cluster_ids=[1, 2, 3], file_count=5)
        level0_analysis = AnalysisInsights(
            description="Top level",
            components=[agents_component],
            components_relations=[],
        )
        level0_expandable = plan_analysis(level0_analysis, parent_had_clusters=True)
        self.assertEqual(len(level0_expandable), 1)
        self.assertEqual(level0_expandable[0].name, "Agents")

        # Level 1: Sub-component from "Agents" - file-level, no clusters
        details_agent = Component(
            name="DetailsAgent",
            description="Details agent module",
            key_entities=[],
            source_cluster_ids=[],  # No clusters at this level
            file_methods=[FileMethodGroup(file_path="agents/details_agent.py", methods=[])],
        )
        level1_analysis = AnalysisInsights(
            description="Agents detail",
            components=[details_agent],
            components_relations=[],
        )
        # Parent (Agents) had clusters
        parent_had_clusters = bool(agents_component.source_cluster_ids)
        level1_expandable = plan_analysis(level1_analysis, parent_had_clusters=parent_had_clusters)
        self.assertEqual(len(level1_expandable), 1)  # Can expand to show file internals

        # Level 2: Sub-component from "DetailsAgent" - no clusters, no files
        run_method = Component(
            name="run_method",
            description="Main run method",
            key_entities=[],
            source_cluster_ids=[],
            file_methods=[],  # Methods don't have files
        )
        level2_analysis = AnalysisInsights(
            description="DetailsAgent internals",
            components=[run_method],
            components_relations=[],
        )
        # Parent (DetailsAgent) had no clusters
        parent_had_clusters = bool(details_agent.source_cluster_ids)
        level2_expandable = plan_analysis(level2_analysis, parent_had_clusters=parent_had_clusters)
        self.assertEqual(len(level2_expandable), 0)  # Should NOT expand - we're at leaf


if __name__ == "__main__":
    unittest.main()
