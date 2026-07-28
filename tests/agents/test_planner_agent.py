"""Tests for the deterministic expansion gates in planner_agent — no LLM calls."""

import unittest

import networkx as nx

from agents.planner_agent import (
    MAX_LEAF_FILES,
    MAX_LEAF_METHODS,
    component_is_separable,
    get_expandable_components,
    leaf_load,
    should_expand_component,
)
from agents.agent_responses import (
    AnalysisInsights,
    Component,
    SourceCodeReference,
)
from agents.file_index_models import FileMethodGroup, MethodEntry
from static_analyzer.graph import METHOD_LEVEL_STRATEGY, ClusterResult


class TestShouldExpandComponent(unittest.TestCase):
    """Tests for should_expand_component function."""

    def _make_component(
        self,
        name: str = "TestComponent",
        cluster_ids: list[int] | None = None,
        file_count: int = 5,
        method_count: int = 5,
    ) -> Component:
        """Helper to create test components.

        Args:
            name: Component name
            cluster_ids: Source cluster IDs
            file_count: Number of file groups to create
            method_count: Total number of methods distributed across files
        """
        ref = SourceCodeReference(
            qualified_name=f"{name}.Class",
            reference_file=f"{name.lower()}.py",
            reference_start_line=1,
            reference_end_line=10,
        )
        # Distribute methods across files
        file_methods = []
        for i in range(file_count):
            # Assign roughly equal methods to each file
            methods_for_file = method_count // file_count if file_count > 0 else 0
            # Give extra methods to the first file if there's a remainder
            if i == 0 and file_count > 0:
                methods_for_file += method_count % file_count
            methods = [
                MethodEntry(
                    qualified_name=f"{name}.file{i}.method{j}",
                    start_line=j * 10 + 1,
                    end_line=j * 10 + 9,
                    node_type="METHOD",
                )
                for j in range(methods_for_file)
            ]
            file_methods.append(FileMethodGroup(file_path=f"file{i}.py", methods=methods))
        return Component(
            name=name,
            description=f"Test component {name}",
            key_entities=[ref],
            source_cluster_ids=[str(cluster_id) for cluster_id in cluster_ids or []],
            file_methods=file_methods,
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

    def test_no_expand_clusters_but_no_files(self):
        """Component with clusters but no file_methods should not expand."""
        component = self._make_component(cluster_ids=[1, 2], file_count=0)
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
            file_methods=[
                FileMethodGroup(
                    file_path="agents/details_agent.py",
                    methods=[
                        MethodEntry(qualified_name="DetailsAgent.run", start_line=1, end_line=10, node_type="METHOD"),
                        MethodEntry(
                            qualified_name="DetailsAgent.expand", start_line=11, end_line=20, node_type="METHOD"
                        ),
                    ],
                )
            ],  # Single file with methods
        )
        # Parent (Agents component) had clusters -> can expand to explain file internals
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
        # Parent (DetailsAgent) had no clusters -> we're at leaf level
        self.assertFalse(should_expand_component(component, parent_had_clusters=False))


class TestPlanAnalysis(unittest.TestCase):
    """Tests for plan_analysis function."""

    def _make_component(
        self,
        name: str,
        cluster_ids: list[int] | None = None,
        file_count: int = 5,
        method_count: int = 5,
    ) -> Component:
        """Helper to create test components.

        Args:
            name: Component name
            cluster_ids: Source cluster IDs
            file_count: Number of file groups to create
            method_count: Total number of methods distributed across files
        """
        file_methods = []
        for i in range(file_count):
            # Assign roughly equal methods to each file
            methods_for_file = method_count // file_count if file_count > 0 else 0
            # Give extra methods to the first file if there's a remainder
            if i == 0 and file_count > 0:
                methods_for_file += method_count % file_count
            methods = [
                MethodEntry(
                    qualified_name=f"{name}.file{i}.method{j}",
                    start_line=j * 10 + 1,
                    end_line=j * 10 + 9,
                    node_type="METHOD",
                )
                for j in range(methods_for_file)
            ]
            file_methods.append(FileMethodGroup(file_path=f"{name.lower()}_file{i}.py", methods=methods))
        return Component(
            name=name,
            description=f"Test component {name}",
            key_entities=[],
            source_cluster_ids=[str(cluster_id) for cluster_id in cluster_ids or []],
            file_methods=file_methods,
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
        result = get_expandable_components(analysis)

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

        # Parent had clusters -> both should expand
        result = get_expandable_components(analysis, parent_had_clusters=True)
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

        # Parent had no clusters -> these are leaves
        result = get_expandable_components(analysis, parent_had_clusters=False)
        self.assertEqual(len(result), 0)

    def test_plan_analysis_empty_components(self):
        """Empty component list should return empty result."""
        analysis = AnalysisInsights(
            description="Empty analysis",
            components=[],
            components_relations=[],
        )

        result = get_expandable_components(analysis)
        self.assertEqual(len(result), 0)

    def test_hierarchical_expansion_scenario(self):
        """
        Test realistic hierarchical expansion:
        - Level 0: "Agents" component with clusters -> expand
        - Level 1: "DetailsAgent" file-level (no clusters, 1 file) -> expand (parent had clusters)
        - Level 2: "run_method" (no clusters, no files) -> DON'T expand (parent had no clusters)
        """
        # Level 0: Top-level component with clusters
        agents_component = self._make_component("Agents", cluster_ids=[1, 2, 3], file_count=5)
        level0_analysis = AnalysisInsights(
            description="Top level",
            components=[agents_component],
            components_relations=[],
        )
        level0_expandable = get_expandable_components(level0_analysis, parent_had_clusters=True)
        self.assertEqual(len(level0_expandable), 1)
        self.assertEqual(level0_expandable[0].name, "Agents")

        # Level 1: Sub-component from "Agents" - file-level, no clusters
        details_agent = Component(
            name="DetailsAgent",
            description="Details agent module",
            key_entities=[],
            source_cluster_ids=[],  # No clusters at this level
            file_methods=[
                FileMethodGroup(
                    file_path="agents/details_agent.py",
                    methods=[
                        MethodEntry(qualified_name="DetailsAgent.run", start_line=1, end_line=50, node_type="METHOD"),
                        MethodEntry(
                            qualified_name="DetailsAgent.expand", start_line=51, end_line=100, node_type="METHOD"
                        ),
                    ],
                )
            ],
        )
        level1_analysis = AnalysisInsights(
            description="Agents detail",
            components=[details_agent],
            components_relations=[],
        )
        # Parent (Agents) had clusters
        parent_had_clusters = bool(agents_component.source_cluster_ids)
        level1_expandable = get_expandable_components(level1_analysis, parent_had_clusters=parent_had_clusters)
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
        level2_expandable = get_expandable_components(level2_analysis, parent_had_clusters=parent_had_clusters)
        self.assertEqual(len(level2_expandable), 0)  # Should NOT expand - we're at leaf


class TestComponentIsSeparable(unittest.TestCase):
    """The modularity + method-count gate deciding whether a component splits further."""

    @staticmethod
    def _subgraph(n_blocks, clusters_per_block, methods_per_cluster, cohesive=False, cross_ratio=0.0):
        """Build (cluster_results, cfg_graphs) for a component subgraph.

        Non-cohesive: clusters chained within a block, blocks weakly bridged (clean split).
        Cohesive: every cluster wired to every other (one blob, no boundary).
        ``cross_ratio`` is the fraction of cross-block pairs that also get an edge; raising
        it blurs the block boundary and drives the split's modularity toward zero.
        """
        n = n_blocks * clusters_per_block
        clusters, cluster_to_files, file_to_clusters = {}, {}, {}
        graph = nx.DiGraph()
        for cid in range(1, n + 1):
            members = {f"n{cid}_{j}" for j in range(methods_per_cluster)}
            clusters[cid] = members
            cluster_to_files[cid] = {f"/repo/c{cid}.py"}
            file_to_clusters[f"/repo/c{cid}.py"] = {cid}
            for m in members:
                graph.add_node(m, file_path=f"/repo/c{cid}.py")
        if cohesive:
            for a in range(1, n + 1):
                for b in range(1, n + 1):
                    if a != b:
                        graph.add_edge(f"n{a}_0", f"n{b}_1")
        else:
            for cid in range(1, n + 1):
                block = (cid - 1) // clusters_per_block
                for other in range(1, n + 1):
                    if other != cid and (other - 1) // clusters_per_block == block:
                        graph.add_edge(f"n{cid}_0", f"n{other}_1")
            for block in range(n_blocks - 1):
                graph.add_edge(f"n{block * clusters_per_block + 1}_0", f"n{(block + 1) * clusters_per_block + 1}_1")
            if cross_ratio:
                cross_pairs = [
                    (a, b)
                    for a in range(1, n + 1)
                    for b in range(1, n + 1)
                    if (a - 1) // clusters_per_block != (b - 1) // clusters_per_block
                ]
                step = max(1, int(1 / cross_ratio))
                for index, (a, b) in enumerate(cross_pairs):
                    if index % step == 0:
                        graph.add_edge(f"n{a}_0", f"n{b}_2")
        cr = ClusterResult(
            clusters=clusters, cluster_to_files=cluster_to_files, file_to_clusters=file_to_clusters, strategy="t"
        )
        return {"python": cr}, {"python": graph}

    def test_large_separable_component_expands(self):
        crs, cfgs = self._subgraph(n_blocks=3, clusters_per_block=3, methods_per_cluster=4)  # 36 methods
        self.assertTrue(component_is_separable(crs, cfgs, load=0.3))

    def test_large_cohesive_component_is_leaf(self):
        crs, cfgs = self._subgraph(n_blocks=1, clusters_per_block=6, methods_per_cluster=6, cohesive=True)  # 36 methods
        self.assertFalse(component_is_separable(crs, cfgs, load=0.3))

    def test_small_component_is_leaf_regardless_of_structure(self):
        # Strong structure but only 9 methods — below the min-method gate.
        crs, cfgs = self._subgraph(n_blocks=3, clusters_per_block=1, methods_per_cluster=3)  # 9 methods
        self.assertFalse(component_is_separable(crs, cfgs, load=0.3))

    def test_method_level_expansion_does_not_masquerade_as_structure(self):
        # A subgraph with too few natural clusters is re-expanded to one synthetic cluster
        # per method upstream. That meta-graph is the raw call graph, whose modularity is
        # not comparable to the threshold, so it must not be read as a split worth making.
        crs, cfgs = self._subgraph(n_blocks=3, clusters_per_block=3, methods_per_cluster=4)
        for cr in crs.values():
            cr.strategy = METHOD_LEVEL_STRATEGY
        self.assertFalse(component_is_separable(crs, cfgs, load=0.3))

    def test_bar_eases_as_the_component_approaches_the_leaf_ceiling(self):
        # Weak but real structure: not worth splitting a small component over, worth
        # splitting a component that is nearly too big to read whole.
        crs, cfgs = self._subgraph(n_blocks=3, clusters_per_block=3, methods_per_cluster=4, cross_ratio=0.34)
        self.assertFalse(component_is_separable(crs, cfgs, load=0.1))
        self.assertTrue(component_is_separable(crs, cfgs, load=0.8))


class TestLeafLoad(unittest.TestCase):
    """leaf_load measures a component against the leaf ceiling on whichever axis is fuller."""

    @staticmethod
    def _component(files: int, methods_per_file: int) -> Component:
        return Component(
            name="C",
            description="",
            key_entities=[],
            file_methods=[
                FileMethodGroup(
                    file_path=f"f{i}.py",
                    methods=[
                        MethodEntry(qualified_name=f"f{i}.m{j}", start_line=1, end_line=2, node_type="METHOD")
                        for j in range(methods_per_file)
                    ],
                )
                for i in range(files)
            ],
        )

    def test_small_component_is_well_under_the_ceiling(self):
        self.assertLess(leaf_load(self._component(files=3, methods_per_file=4)), 1.0)

    def test_file_count_alone_can_exceed_the_ceiling(self):
        # One method each, but spread over more files than a single box should hold.
        self.assertGreaterEqual(leaf_load(self._component(files=MAX_LEAF_FILES + 1, methods_per_file=1)), 1.0)

    def test_method_count_alone_can_exceed_the_ceiling(self):
        self.assertGreaterEqual(leaf_load(self._component(files=2, methods_per_file=MAX_LEAF_METHODS)), 1.0)

    def test_empty_component_has_zero_load(self):
        self.assertEqual(leaf_load(self._component(files=0, methods_per_file=0)), 0.0)


class TestSeparablePredicate(unittest.TestCase):
    """get_expandable_components honours the optional separability predicate."""

    @staticmethod
    def _expandable_component(name: str) -> Component:
        methods = [MethodEntry(qualified_name=f"{name}.m", start_line=1, end_line=9, node_type="METHOD")]
        return Component(
            name=name,
            description=name,
            key_entities=[],
            source_cluster_ids=["1"],
            file_methods=[FileMethodGroup(file_path=f"{name}.py", methods=methods)],
        )

    def test_predicate_filters_cohesive_components(self):
        analysis = AnalysisInsights(
            description="d",
            components=[self._expandable_component("Keep"), self._expandable_component("Drop")],
            components_relations=[],
        )
        result = get_expandable_components(analysis, separable=lambda c: c.name == "Keep")
        self.assertEqual([c.name for c in result], ["Keep"])

    def test_no_predicate_preserves_structural_behavior(self):
        analysis = AnalysisInsights(
            description="d",
            components=[self._expandable_component("A"), self._expandable_component("B")],
            components_relations=[],
        )
        self.assertEqual(len(get_expandable_components(analysis)), 2)


if __name__ == "__main__":
    unittest.main()
