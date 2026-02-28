"""Tests for ReferenceResolverMixin using real key_entities from analysis.json.

These tests mirror the actual CodeBoarding repository structure and the
references produced by the LLM to verify that fix_source_code_reference_lines
correctly resolves every key_entity.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agents.agent_responses import AnalysisInsights, Component, FileMethodGroup, SourceCodeReference
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Node
from static_analyzer.reference_resolve_mixin import ReferenceResolverMixin


class ConcreteResolver(ReferenceResolverMixin):
    """Concrete test implementation of the mixin."""

    def __init__(self, repo_dir: Path, static_analysis: StaticAnalysisResults):
        super().__init__(repo_dir, static_analysis)

    def _parse_invoke(self, prompt, type):
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Key entities taken directly from a real CodeBoarding analysis.json run.
ANALYSIS_KEY_ENTITIES: list[tuple[str, str]] = [
    ("core.registry.Registry", "core/registry.py"),
    ("core.plugin_loader.PluginLoader", "core/plugin_loader.py"),
    ("agents.llm_config.LLMConfig", "agents/llm_config.py"),
    ("core.protocols.StaticAnalyzer", "core/protocols.py"),
    ("caching.meta_cache.MetaCache", "caching/meta_cache.py"),
    ("agents.dependency_discovery.DependencyDiscovery", "agents/dependency_discovery.py"),
    ("agents.tools.read_cfg.ReadCFG", "agents/tools/read_cfg.py"),
    ("diagram_analysis.incremental.impact_analyzer.ImpactAnalyzer", "diagram_analysis/incremental/impact_analyzer.py"),
    ("agents.abstraction_agent.AbstractionAgent", "agents/abstraction_agent.py"),
    ("agents.cluster_methods_mixin.ClusterMethodsMixin", "agents/cluster_methods_mixin.py"),
    ("agents.meta_agent.MetaAgent", "agents/meta_agent.py"),
    ("agents.validation.ValidationContext", "agents/validation.py"),
    ("diagram_analysis.analysis_json.UnifiedAnalysisJson", "diagram_analysis/analysis_json.py"),
    ("diagram_analysis.diagram_generator.DiagramGenerator", "diagram_analysis/diagram_generator.py"),
    ("diagram_analysis.manifest.Manifest", "diagram_analysis/manifest.py"),
    ("diagram_analysis.incremental.io_utils.IOUtils", "diagram_analysis/incremental/io_utils.py"),
    ("github_action.main", "github_action.py"),
    ("core.registry.Registries", "core/registry.py"),
]


def _make_repo_tree(root: Path) -> None:
    """Create the file tree expected by the key entities above."""
    files = {ref_file for _, ref_file in ANALYSIS_KEY_ENTITIES}
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# stub for {rel}\nclass Stub: pass\n")


def _make_node(repo_dir: Path, qname: str, rel_file: str) -> Node:
    """Create a Node matching the static analysis expectations."""
    return Node(
        fully_qualified_name=qname,
        file_path=str(repo_dir / rel_file),
        line_start=0,
        line_end=5,
        node_type=5,
    )


def _make_file_methods(file_paths: list[str]) -> list[FileMethodGroup]:
    """Create FileMethodGroup objects from file paths for testing."""
    return [FileMethodGroup(file_path=fp, methods=[]) for fp in file_paths]


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestRelativePathAlreadyResolved(unittest.TestCase):
    """The LLM provides a correct *relative* reference_file.

    fix_source_code_reference_lines should recognise this as already resolved
    (joining with repo_dir) instead of re-resolving or dropping the reference.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _make_repo_tree(self.tmp)
        self.sa = MagicMock(spec=StaticAnalysisResults)
        self.sa.get_languages.return_value = ["python"]
        self.sa.get_reference.side_effect = ValueError("not found")
        self.sa.get_loose_reference.side_effect = Exception("not found")
        self.resolver = ConcreteResolver(self.tmp, self.sa)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_all_analysis_json_refs_preserved(self):
        """Every key_entity from analysis.json should survive resolution unchanged."""
        refs = [SourceCodeReference(qualified_name=qn, reference_file=rf) for qn, rf in ANALYSIS_KEY_ENTITIES]
        comp = Component(name="C", description="d", key_entities=refs)
        analysis = AnalysisInsights(description="d", components=[comp], components_relations=[])

        result = self.resolver.fix_source_code_reference_lines(analysis)

        surviving_qnames = {r.qualified_name for r in result.components[0].key_entities}
        expected_qnames = {qn for qn, _ in ANALYSIS_KEY_ENTITIES}
        self.assertEqual(surviving_qnames, expected_qnames, "Some key_entities were dropped")

    def test_relative_paths_stay_relative(self):
        """After resolution the reference_file should be a relative path."""
        refs = [SourceCodeReference(qualified_name=qn, reference_file=rf) for qn, rf in ANALYSIS_KEY_ENTITIES]
        comp = Component(name="C", description="d", key_entities=refs)
        analysis = AnalysisInsights(description="d", components=[comp], components_relations=[])

        result = self.resolver.fix_source_code_reference_lines(analysis)

        for ref in result.components[0].key_entities:
            self.assertFalse(
                os.path.isabs(ref.reference_file),
                f"{ref.qualified_name} has absolute reference_file: {ref.reference_file}",
            )


class TestResolveFromQualifiedNameOnly(unittest.TestCase):
    """The LLM provides only qualified_name (reference_file is None).

    The resolver must figure out the correct file.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _make_repo_tree(self.tmp)
        self.sa = MagicMock(spec=StaticAnalysisResults)
        self.sa.get_languages.return_value = ["python"]
        self.resolver = ConcreteResolver(self.tmp, self.sa)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_exact_match_resolves(self):
        """Static-analysis exact match should populate reference_file and lines."""
        qname = "core.registry.Registry"
        expected_file = "core/registry.py"
        node = _make_node(self.tmp, qname, expected_file)

        self.sa.get_reference.return_value = node

        ref = SourceCodeReference(qualified_name=qname, reference_file=None)
        comp = Component(name="C", description="d", key_entities=[ref])
        analysis = AnalysisInsights(description="d", components=[comp], components_relations=[])

        result = self.resolver.fix_source_code_reference_lines(analysis)

        self.assertEqual(len(result.components[0].key_entities), 1)
        resolved = result.components[0].key_entities[0]
        self.assertEqual(resolved.reference_file, expected_file)
        self.assertIsNotNone(resolved.reference_start_line)

    def test_loose_match_resolves(self):
        """Loose match should work when exact match fails."""
        qname = "agents.abstraction_agent.AbstractionAgent"
        expected_file = "agents/abstraction_agent.py"
        node = _make_node(self.tmp, qname, expected_file)

        self.sa.get_reference.side_effect = ValueError("not found")
        self.sa.get_loose_reference.return_value = (qname, node)

        ref = SourceCodeReference(qualified_name=qname, reference_file=None)
        comp = Component(name="C", description="d", key_entities=[ref])
        analysis = AnalysisInsights(description="d", components=[comp], components_relations=[])

        result = self.resolver.fix_source_code_reference_lines(analysis)

        self.assertEqual(len(result.components[0].key_entities), 1)
        self.assertEqual(result.components[0].key_entities[0].reference_file, expected_file)

    def test_file_path_fallback_resolves(self):
        """When static analysis fails, qualified_name → file path conversion should work.

        e.g. agents.llm_config.LLMConfig → agents/llm_config.py  (strip last segment, add .py)
        """
        qname = "agents.llm_config.LLMConfig"
        expected_file = "agents/llm_config.py"

        self.sa.get_reference.side_effect = ValueError("not found")
        self.sa.get_loose_reference.side_effect = Exception("not found")

        ref = SourceCodeReference(qualified_name=qname, reference_file=None)
        comp = Component(
            name="C", description="d", key_entities=[ref], file_methods=_make_file_methods([expected_file])
        )
        analysis = AnalysisInsights(description="d", components=[comp], components_relations=[])

        result = self.resolver.fix_source_code_reference_lines(analysis)

        self.assertEqual(len(result.components[0].key_entities), 1)
        resolved = result.components[0].key_entities[0]
        self.assertEqual(resolved.reference_file, expected_file)

    def test_deep_nested_qname_resolves(self):
        """Deeply nested qualified names should resolve.

        e.g. diagram_analysis.incremental.impact_analyzer.ImpactAnalyzer
             → diagram_analysis/incremental/impact_analyzer.py
        """
        qname = "diagram_analysis.incremental.impact_analyzer.ImpactAnalyzer"
        expected_file = "diagram_analysis/incremental/impact_analyzer.py"

        self.sa.get_reference.side_effect = ValueError("not found")
        self.sa.get_loose_reference.side_effect = Exception("not found")

        ref = SourceCodeReference(qualified_name=qname, reference_file=None)
        comp = Component(
            name="C", description="d", key_entities=[ref], file_methods=_make_file_methods([expected_file])
        )
        analysis = AnalysisInsights(description="d", components=[comp], components_relations=[])

        result = self.resolver.fix_source_code_reference_lines(analysis)

        self.assertEqual(len(result.components[0].key_entities), 1)
        self.assertEqual(result.components[0].key_entities[0].reference_file, expected_file)

    def test_module_level_function_resolves(self):
        """Module-level function: github_action.main → github_action.py"""
        qname = "github_action.main"
        expected_file = "github_action.py"

        self.sa.get_reference.side_effect = ValueError("not found")
        self.sa.get_loose_reference.side_effect = Exception("not found")

        ref = SourceCodeReference(qualified_name=qname, reference_file=None)
        comp = Component(
            name="C", description="d", key_entities=[ref], file_methods=_make_file_methods([expected_file])
        )
        analysis = AnalysisInsights(description="d", components=[comp], components_relations=[])

        result = self.resolver.fix_source_code_reference_lines(analysis)

        self.assertEqual(len(result.components[0].key_entities), 1)
        self.assertEqual(result.components[0].key_entities[0].reference_file, expected_file)


class TestRelativePathCWDBug(unittest.TestCase):
    """Expose bug: os.path.exists(relative_path) is CWD-dependent.

    When CWD == repo_dir, the relative path check passes and the reference
    is skipped — no line numbers are ever populated.  When CWD != repo_dir
    the same reference falls through to resolution and gets different treatment.
    The resolver should behave identically regardless of CWD.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _make_repo_tree(self.tmp)
        self.sa = MagicMock(spec=StaticAnalysisResults)
        self.sa.get_languages.return_value = ["python"]
        self.resolver = ConcreteResolver(self.tmp, self.sa)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_relative_ref_resolved_regardless_of_cwd(self):
        """A relative reference_file must be recognized even when CWD != repo_dir.

        The check should use repo_dir to resolve the relative path, not rely on
        os.path.exists with a bare relative path.
        """
        self.sa.get_reference.side_effect = ValueError("not found")
        self.sa.get_loose_reference.side_effect = Exception("not found")

        ref = SourceCodeReference(qualified_name="core.registry.Registry", reference_file="core/registry.py")
        comp = Component(name="C", description="d", key_entities=[ref])
        analysis = AnalysisInsights(description="d", components=[comp], components_relations=[])

        # Run from a different CWD to ensure the resolver doesn't depend on it
        original_cwd = os.getcwd()
        try:
            os.chdir(tempfile.mkdtemp())
            result = self.resolver.fix_source_code_reference_lines(analysis)
        finally:
            os.chdir(original_cwd)

        self.assertEqual(len(result.components[0].key_entities), 1)
        self.assertEqual(result.components[0].key_entities[0].reference_file, "core/registry.py")

    def test_line_numbers_populated_from_static_analysis(self):
        """When static analysis knows the entity, line numbers must be populated.

        Even if reference_file is already set, we should still resolve line numbers
        from static analysis when they are missing.
        """
        node = _make_node(self.tmp, "core.registry.Registry", "core/registry.py")
        self.sa.get_reference.return_value = node

        ref = SourceCodeReference(
            qualified_name="core.registry.Registry",
            reference_file="core/registry.py",
            reference_start_line=None,
            reference_end_line=None,
        )
        comp = Component(name="C", description="d", key_entities=[ref])
        analysis = AnalysisInsights(description="d", components=[comp], components_relations=[])

        result = self.resolver.fix_source_code_reference_lines(analysis)

        resolved = result.components[0].key_entities[0]
        self.assertIsNotNone(resolved.reference_start_line, "start_line should be populated from static analysis")
        self.assertIsNotNone(resolved.reference_end_line, "end_line should be populated from static analysis")

    def test_file_path_resolution_populates_no_line_numbers(self):
        """Expose: file-path fallback resolves the file but leaves line numbers None.

        This is acceptable but should be documented — line numbers are only
        available through static analysis (exact/loose match).
        """
        self.sa.get_reference.side_effect = ValueError("not found")
        self.sa.get_loose_reference.side_effect = Exception("not found")

        ref = SourceCodeReference(qualified_name="core.registry.Registry", reference_file=None)
        comp = Component(
            name="C", description="d", key_entities=[ref], file_methods=_make_file_methods(["core/registry.py"])
        )
        analysis = AnalysisInsights(description="d", components=[comp], components_relations=[])

        result = self.resolver.fix_source_code_reference_lines(analysis)

        self.assertEqual(len(result.components[0].key_entities), 1)
        resolved = result.components[0].key_entities[0]
        # File is found but line numbers are NOT populated by file-path fallback
        self.assertIsNotNone(resolved.reference_file)
        self.assertIsNone(resolved.reference_start_line)
        self.assertIsNone(resolved.reference_end_line)


class TestMultiComponentAnalysis(unittest.TestCase):
    """Simulate the full analysis.json with multiple components and verify 100% resolution."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _make_repo_tree(self.tmp)
        self.sa = MagicMock(spec=StaticAnalysisResults)
        self.sa.get_languages.return_value = ["python"]
        self.resolver = ConcreteResolver(self.tmp, self.sa)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_analysis(self, ref_file_present: bool) -> AnalysisInsights:
        """Build an AnalysisInsights matching analysis.json structure.

        Args:
            ref_file_present: if True, set reference_file on every ref (LLM gave it);
                              if False, set reference_file=None (resolve from qname only).
        """
        component_groups: dict[str, list[tuple[str, str]]] = {
            "Core Infrastructure & Registry": [
                ("core.registry.Registry", "core/registry.py"),
                ("core.plugin_loader.PluginLoader", "core/plugin_loader.py"),
                ("agents.llm_config.LLMConfig", "agents/llm_config.py"),
                ("core.protocols.StaticAnalyzer", "core/protocols.py"),
            ],
            "Static Analysis & Context Engine": [
                ("caching.meta_cache.MetaCache", "caching/meta_cache.py"),
                ("agents.dependency_discovery.DependencyDiscovery", "agents/dependency_discovery.py"),
                ("agents.tools.read_cfg.ReadCFG", "agents/tools/read_cfg.py"),
                (
                    "diagram_analysis.incremental.impact_analyzer.ImpactAnalyzer",
                    "diagram_analysis/incremental/impact_analyzer.py",
                ),
            ],
            "AI Agentic Layer": [
                ("agents.abstraction_agent.AbstractionAgent", "agents/abstraction_agent.py"),
                ("agents.cluster_methods_mixin.ClusterMethodsMixin", "agents/cluster_methods_mixin.py"),
                ("agents.meta_agent.MetaAgent", "agents/meta_agent.py"),
                ("agents.validation.ValidationContext", "agents/validation.py"),
            ],
            "Output & Visualization Engine": [
                ("diagram_analysis.analysis_json.UnifiedAnalysisJson", "diagram_analysis/analysis_json.py"),
                ("diagram_analysis.diagram_generator.DiagramGenerator", "diagram_analysis/diagram_generator.py"),
                ("diagram_analysis.manifest.Manifest", "diagram_analysis/manifest.py"),
                ("diagram_analysis.incremental.io_utils.IOUtils", "diagram_analysis/incremental/io_utils.py"),
            ],
            "CLI & Integration Layer": [
                ("github_action.main", "github_action.py"),
                ("core.registry.Registries", "core/registry.py"),
            ],
        }
        components = []
        for name, entities in component_groups.items():
            refs = []
            file_paths = []
            for qn, rf in entities:
                refs.append(
                    SourceCodeReference(
                        qualified_name=qn,
                        reference_file=rf if ref_file_present else None,
                    )
                )
                if rf not in file_paths:
                    file_paths.append(rf)
            components.append(
                Component(name=name, description="d", key_entities=refs, file_methods=_make_file_methods(file_paths))
            )

        return AnalysisInsights(description="CodeBoarding analysis", components=components, components_relations=[])

    def test_all_refs_resolved_with_reference_file(self):
        """When LLM provides reference_file, every entity must survive."""
        self.sa.get_reference.side_effect = ValueError("not found")
        self.sa.get_loose_reference.side_effect = Exception("not found")

        analysis = self._build_analysis(ref_file_present=True)
        result = self.resolver.fix_source_code_reference_lines(analysis)

        total = sum(len(c.key_entities) for c in result.components)
        self.assertEqual(total, len(ANALYSIS_KEY_ENTITIES), "Some key_entities were dropped")

    def test_all_refs_resolved_with_exact_match(self):
        """When LLM provides no reference_file, exact match should resolve all."""
        # Build nodes for every key entity
        nodes = {}
        for qn, rf in ANALYSIS_KEY_ENTITIES:
            nodes[qn] = _make_node(self.tmp, qn, rf)

        self.sa.get_reference.side_effect = lambda lang, qn: nodes.get(qn) or (_ for _ in ()).throw(ValueError("nope"))

        analysis = self._build_analysis(ref_file_present=False)
        result = self.resolver.fix_source_code_reference_lines(analysis)

        total = sum(len(c.key_entities) for c in result.components)
        self.assertEqual(total, len(ANALYSIS_KEY_ENTITIES))

        for comp in result.components:
            for ref in comp.key_entities:
                self.assertIsNotNone(ref.reference_file, f"{ref.qualified_name} unresolved")
                self.assertFalse(os.path.isabs(ref.reference_file))

    def test_output_paths_are_all_relative(self):
        """No matter the resolution strategy, output paths must be relative."""
        self.sa.get_reference.side_effect = ValueError("not found")
        self.sa.get_loose_reference.side_effect = Exception("not found")

        analysis = self._build_analysis(ref_file_present=True)
        result = self.resolver.fix_source_code_reference_lines(analysis)

        for comp in result.components:
            for ref in comp.key_entities:
                self.assertFalse(
                    os.path.isabs(ref.reference_file),
                    f"{ref.qualified_name} -> {ref.reference_file} is absolute",
                )


if __name__ == "__main__":
    unittest.main()
