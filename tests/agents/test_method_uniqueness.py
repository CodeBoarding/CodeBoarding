"""Tests for method uniqueness across sibling components.

Reproduces the nanoclaw bug where the same file (e.g., container/agent-runner/src/index.ts)
has ALL its methods assigned to multiple sibling components.

ROOT CAUSE: The static analysis produces duplicate qualified-name aliases for the
same physical symbol (e.g., `src.index.funcA` and `container.agent-runner.src.index.funcA`
both point to the same function at lines 1-10 in the same file). These aliases end up in
different Louvain clusters, which get assigned to different components. Since each alias
is a distinct Node, `_assign_nodes_to_components` correctly assigns each alias to exactly
one component. However, `_build_file_methods_from_nodes` deduplicates by
(start_line, end_line, type, short_name) — so both aliases produce the same MethodEntry
in the output. Result: the same deduplicated method appears in BOTH components.

The invariant: at the same tree level, a method (identified by its deduplicated key:
start_line + end_line + type + short_name) must appear in at most ONE component's
file_methods.
"""

import unittest
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    SourceCodeReference,
)
from agents.cluster_methods_mixin import ClusterMethodsMixin
from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph, ClusterResult
from static_analyzer.node import Node


class MockMixin(ClusterMethodsMixin):
    """Concrete implementation for testing the mixin."""

    def __init__(self, repo_dir: Path, static_analysis: MagicMock):
        self.repo_dir = repo_dir
        self.static_analysis = static_analysis


def _assert_no_duplicate_methods(test_case: unittest.TestCase, analysis: AnalysisInsights):
    """Assert that no method (by qualified_name) appears in more than one sibling component."""
    method_to_components: dict[str, list[str]] = defaultdict(list)

    for comp in analysis.components:
        for fmg in comp.file_methods:
            for method in fmg.methods:
                method_to_components[method.qualified_name].append(comp.name)

    duplicates = {qn: comps for qn, comps in method_to_components.items() if len(comps) > 1}
    if duplicates:
        lines = [f"  {qn}: {comps}" for qn, comps in sorted(duplicates.items())[:10]]
        detail = "\n".join(lines)
        more = f"\n  ... and {len(duplicates) - 10} more" if len(duplicates) > 10 else ""
        test_case.fail(f"{len(duplicates)} method(s) appear in multiple sibling components:\n{detail}{more}")


def _assert_no_duplicate_methods_by_location(test_case: unittest.TestCase, analysis: AnalysisInsights):
    """Assert that no method (by file + start_line + end_line + type) appears in more than
    one sibling component. This catches the alias-dedup case where two different
    qualified_names resolve to the same physical symbol."""
    location_to_components: dict[tuple[str, int, int, str], list[tuple[str, str]]] = defaultdict(list)

    for comp in analysis.components:
        for fmg in comp.file_methods:
            for method in fmg.methods:
                loc_key = (fmg.file_path, method.start_line, method.end_line, method.node_type)
                location_to_components[loc_key].append((comp.name, method.qualified_name))

    duplicates = {k: v for k, v in location_to_components.items() if len(v) > 1}
    if duplicates:
        lines = []
        for loc, entries in sorted(duplicates.items())[:10]:
            file_path, start, end, ntype = loc
            comps = [(c, qn) for c, qn in entries]
            lines.append(f"  {file_path}:{start}-{end} ({ntype}): {comps}")
        detail = "\n".join(lines)
        more = f"\n  ... and {len(duplicates) - 10} more" if len(duplicates) > 10 else ""
        test_case.fail(f"{len(duplicates)} physical method(s) appear in multiple sibling components:\n{detail}{more}")


class TestMethodUniquenessWithAliases(unittest.TestCase):
    """Reproduce the nanoclaw bug: the same physical method has two qualified-name
    aliases that end up in different clusters and hence different components.

    After deduplication in _build_file_methods_from_nodes, both components end up
    with the same MethodEntry, violating the uniqueness invariant.
    """

    def _make_alias_scenario(self):
        """Create a scenario where the same physical methods have two qualified-name
        aliases that end up in different clusters.

        File: container/agent-runner/src/index.ts
            funcA at lines 1-10: two aliases
                - container.agent-runner.src.index.funcA (cluster 0 -> Sandbox)
                - src.index.funcA (cluster 1 -> Orchestration)
            funcB at lines 11-20: two aliases
                - container.agent-runner.src.index.funcB (cluster 0 -> Sandbox)
                - src.index.funcB (cluster 1 -> Orchestration)
            funcC at lines 21-30: two aliases
                - container.agent-runner.src.index.funcC (cluster 0 -> Sandbox)
                - src.index.funcC (cluster 1 -> Orchestration)

        After deduplication by (start_line, end_line, type, short_name), each pair
        of aliases produces one MethodEntry — which currently appears in BOTH components.
        """
        repo_dir = Path("/test/repo")
        cfg = CallGraph(language="typescript")

        shared_file = "/test/repo/container/agent-runner/src/index.ts"

        # Alias pair 1: funcA
        cfg.add_node(Node("container.agent-runner.src.index.funcA", NodeType.FUNCTION, shared_file, 1, 10))
        cfg.add_node(Node("src.index.funcA", NodeType.FUNCTION, shared_file, 1, 10))

        # Alias pair 2: funcB
        cfg.add_node(Node("container.agent-runner.src.index.funcB", NodeType.FUNCTION, shared_file, 11, 20))
        cfg.add_node(Node("src.index.funcB", NodeType.FUNCTION, shared_file, 11, 20))

        # Alias pair 3: funcC
        cfg.add_node(Node("container.agent-runner.src.index.funcC", NodeType.FUNCTION, shared_file, 21, 30))
        cfg.add_node(Node("src.index.funcC", NodeType.FUNCTION, shared_file, 21, 30))

        # Non-aliased nodes in separate files (one per component)
        sandbox_file = "/test/repo/container/agent-runner/src/ipc.ts"
        orch_file = "/test/repo/src/orchestrator.ts"
        cfg.add_node(Node("src.ipc.handleIpc", NodeType.FUNCTION, sandbox_file, 1, 10))
        cfg.add_node(Node("src.orchestrator.run", NodeType.FUNCTION, orch_file, 1, 10))

        # Add edges to create cluster structure
        cfg.add_edge("container.agent-runner.src.index.funcA", "src.ipc.handleIpc")
        cfg.add_edge("src.index.funcA", "src.orchestrator.run")

        # Cluster result: aliases in different clusters
        cluster_result = ClusterResult(
            clusters={
                0: {
                    "container.agent-runner.src.index.funcA",
                    "container.agent-runner.src.index.funcB",
                    "container.agent-runner.src.index.funcC",
                    "src.ipc.handleIpc",
                },
                1: {
                    "src.index.funcA",
                    "src.index.funcB",
                    "src.index.funcC",
                    "src.orchestrator.run",
                },
            },
            cluster_to_files={
                0: {shared_file, sandbox_file},
                1: {shared_file, orch_file},
            },
            file_to_clusters={
                shared_file: {0, 1},
                sandbox_file: {0},
                orch_file: {1},
            },
            strategy="test",
        )

        comp_sandbox = Component(
            name="Sandbox Agent",
            description="Sandbox agent component",
            key_entities=[SourceCodeReference(qualified_name="src.ipc.handleIpc")],
            source_group_names=["Sandbox Group"],
            source_cluster_ids=[0],
            component_id="1",
        )
        comp_orch = Component(
            name="Orchestration Engine",
            description="Orchestration component",
            key_entities=[SourceCodeReference(qualified_name="src.orchestrator.run")],
            source_group_names=["Orchestration Group"],
            source_cluster_ids=[1],
            component_id="2",
        )

        analysis = AnalysisInsights(
            description="Test analysis with aliased methods",
            components=[comp_sandbox, comp_orch],
            components_relations=[],
        )

        static_analysis = MagicMock()
        static_analysis.get_cfg.return_value = cfg
        static_analysis.get_languages.return_value = ["typescript"]

        mixin = MockMixin(repo_dir=repo_dir, static_analysis=static_analysis)

        return mixin, analysis, {"typescript": cluster_result}

    def test_aliased_methods_not_duplicated_across_components(self):
        """When the same physical method has two qualified-name aliases in different
        clusters/components, the deduplicated method must appear in only ONE component."""
        mixin, analysis, cluster_results = self._make_alias_scenario()

        mixin.populate_file_methods(analysis, cluster_results)

        # Check by location (start_line + end_line + type) — this is the key assertion
        _assert_no_duplicate_methods_by_location(self, analysis)

    def test_aliased_methods_all_assigned(self):
        """All physical methods must be assigned to exactly one component (not lost)."""
        mixin, analysis, cluster_results = self._make_alias_scenario()

        mixin.populate_file_methods(analysis, cluster_results)

        # We have 3 aliased methods + 2 unique = 5 physical methods
        all_locations: set[tuple[str, int, int]] = set()
        for comp in analysis.components:
            for fmg in comp.file_methods:
                for m in fmg.methods:
                    all_locations.add((fmg.file_path, m.start_line, m.end_line))

        # 3 unique physical methods from shared file + 1 from ipc + 1 from orchestrator
        self.assertEqual(
            len(all_locations),
            5,
            f"Expected 5 unique physical methods, got {len(all_locations)}: {all_locations}",
        )


class TestMethodUniquenessNanoclaw(unittest.TestCase):
    """Full nanoclaw-like scenario with many aliases spread across multiple components."""

    def test_large_scale_alias_duplication(self):
        """Simulates 20 methods in a monolith file, each with 2 aliases in different
        clusters, spread across 3 components. No physical method should appear in
        more than one component."""
        repo_dir = Path("/test/repo")
        cfg = CallGraph(language="typescript")

        monolith = "/test/repo/container/agent-runner/src/index.ts"
        method_names = [f"func_{i}" for i in range(20)]

        # Each method has two aliases
        for i, name in enumerate(method_names):
            long_alias = f"container.agent-runner.src.index.{name}"
            short_alias = f"src.index.{name}"
            cfg.add_node(Node(long_alias, NodeType.FUNCTION, monolith, i * 10 + 1, i * 10 + 10))
            cfg.add_node(Node(short_alias, NodeType.FUNCTION, monolith, i * 10 + 1, i * 10 + 10))

        # 3 component-specific files
        for comp_idx in range(3):
            f = f"/test/repo/src/comp_{comp_idx}.ts"
            cfg.add_node(Node(f"src.comp_{comp_idx}.main", NodeType.FUNCTION, f, 1, 10))

        # Clusters: long aliases go to components 0,1,2 in round-robin
        # Short aliases go to a DIFFERENT component
        cluster_0_members: set[str] = {"src.comp_0.main"}
        cluster_1_members: set[str] = {"src.comp_1.main"}
        cluster_2_members: set[str] = {"src.comp_2.main"}

        for i, name in enumerate(method_names):
            long_alias = f"container.agent-runner.src.index.{name}"
            short_alias = f"src.index.{name}"

            # Long alias goes to cluster (i % 3)
            target_cluster = i % 3
            # Short alias goes to a different cluster ((i + 1) % 3)
            other_cluster = (i + 1) % 3

            if target_cluster == 0:
                cluster_0_members.add(long_alias)
            elif target_cluster == 1:
                cluster_1_members.add(long_alias)
            else:
                cluster_2_members.add(long_alias)

            if other_cluster == 0:
                cluster_0_members.add(short_alias)
            elif other_cluster == 1:
                cluster_1_members.add(short_alias)
            else:
                cluster_2_members.add(short_alias)

        cluster_result = ClusterResult(
            clusters={
                0: cluster_0_members,
                1: cluster_1_members,
                2: cluster_2_members,
            },
            cluster_to_files={
                0: {monolith, "/test/repo/src/comp_0.ts"},
                1: {monolith, "/test/repo/src/comp_1.ts"},
                2: {monolith, "/test/repo/src/comp_2.ts"},
            },
            file_to_clusters={
                monolith: {0, 1, 2},
                "/test/repo/src/comp_0.ts": {0},
                "/test/repo/src/comp_1.ts": {1},
                "/test/repo/src/comp_2.ts": {2},
            },
            strategy="test",
        )

        components = [
            Component(
                name=f"Component_{idx}",
                description=f"Component {idx}",
                key_entities=[SourceCodeReference(qualified_name=f"src.comp_{idx}.main")],
                source_group_names=[f"Group_{idx}"],
                source_cluster_ids=[idx],
                component_id=str(idx + 1),
            )
            for idx in range(3)
        ]

        analysis = AnalysisInsights(
            description="Large-scale alias test",
            components=components,
            components_relations=[],
        )

        static_analysis = MagicMock()
        static_analysis.get_cfg.return_value = cfg
        static_analysis.get_languages.return_value = ["typescript"]

        mixin = MockMixin(repo_dir=repo_dir, static_analysis=static_analysis)
        mixin.populate_file_methods(analysis, {"typescript": cluster_result})

        _assert_no_duplicate_methods_by_location(self, analysis)

        # All 23 physical methods (20 from monolith + 3 from comp files) should be assigned
        all_locations: set[tuple[str, int, int]] = set()
        for comp in analysis.components:
            for fmg in comp.file_methods:
                for m in fmg.methods:
                    all_locations.add((fmg.file_path, m.start_line, m.end_line))
        self.assertEqual(len(all_locations), 23)


class TestMethodUniquenessNoAliases(unittest.TestCase):
    """Sanity tests: when there are no aliases, methods should never be duplicated."""

    def test_shared_file_no_aliases_no_duplicates(self):
        """Methods from a shared file with no aliases in different clusters
        must appear in only one component."""
        repo_dir = Path("/test/repo")
        cfg = CallGraph(language="typescript")
        shared_file = "/test/repo/src/shared.ts"
        alpha_file = "/test/repo/src/alpha.ts"

        cfg.add_node(Node("src.shared.funcA", NodeType.FUNCTION, shared_file, 1, 10))
        cfg.add_node(Node("src.shared.funcB", NodeType.FUNCTION, shared_file, 11, 20))
        cfg.add_node(Node("src.shared.funcC", NodeType.FUNCTION, shared_file, 21, 30))
        cfg.add_node(Node("src.shared.funcD", NodeType.FUNCTION, shared_file, 31, 40))
        cfg.add_node(Node("src.shared.funcE", NodeType.FUNCTION, shared_file, 41, 50))  # orphan
        cfg.add_node(Node("src.alpha.funcF", NodeType.FUNCTION, alpha_file, 1, 10))

        cfg.add_edge("src.shared.funcA", "src.shared.funcB")
        cfg.add_edge("src.shared.funcC", "src.shared.funcD")

        cluster_result = ClusterResult(
            clusters={
                0: {"src.shared.funcA", "src.shared.funcB"},
                1: {"src.shared.funcC", "src.shared.funcD"},
                2: {"src.alpha.funcF"},
            },
            cluster_to_files={
                0: {shared_file},
                1: {shared_file},
                2: {alpha_file},
            },
            file_to_clusters={
                shared_file: {0, 1},
                alpha_file: {2},
            },
            strategy="test",
        )

        comp_alpha = Component(
            name="Alpha",
            description="Alpha",
            component_id="1",
            key_entities=[SourceCodeReference(qualified_name="src.shared.funcA")],
            source_group_names=["Alpha"],
            source_cluster_ids=[0, 2],
        )
        comp_beta = Component(
            name="Beta",
            description="Beta",
            component_id="2",
            key_entities=[SourceCodeReference(qualified_name="src.shared.funcC")],
            source_group_names=["Beta"],
            source_cluster_ids=[1],
        )

        analysis = AnalysisInsights(
            description="No-alias test",
            components=[comp_alpha, comp_beta],
            components_relations=[],
        )

        static_analysis = MagicMock()
        static_analysis.get_cfg.return_value = cfg
        static_analysis.get_languages.return_value = ["typescript"]

        mixin = MockMixin(repo_dir=repo_dir, static_analysis=static_analysis)
        mixin.populate_file_methods(analysis, {"typescript": cluster_result})

        _assert_no_duplicate_methods(self, analysis)
        _assert_no_duplicate_methods_by_location(self, analysis)

        # All 6 methods should be assigned
        total = sum(len(m.methods) for c in analysis.components for m in c.file_methods)
        self.assertEqual(total, 6)


if __name__ == "__main__":
    unittest.main()
