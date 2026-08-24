import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, Mock, patch

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    Relation,
    RelationCallSite,
    RelationEdge,
    ScopeUpdateDecision,
    SourceCodeReference,
    assign_component_ids,
)
from agents.file_index_models import FileEntry, FileMethodGroup, MethodEntry
from agents.incremental_results import RecursiveScopeUpdateResult, ScopeRelationContext, ScopeUpdateResult
from agents.relation_edges import index_relation_endpoints
from agents.scope_ids import ROOT_SCOPE_ID
from diagram_analysis.analysis_json import (
    ComponentFileMethodGroupJson,
    ComponentJson,
    RelationJson,
    UnifiedAnalysisJson,
    build_unified_analysis_json,
    from_analysis_to_json,
    from_component_to_json_component,
    parse_unified_analysis,
)
from diagram_analysis.diagram_generator import (
    DiagramGenerator,
    _IncrementalPreparation,
    _MembershipBaseline,
    _component_depth,
    _component_expansion_seeds,
)
from diagram_analysis.exceptions import ClusteringScopeUnavailableError
from diagram_analysis.io_utils import load_analysis_metadata, save_analysis
from static_analyzer.analysis_cache import StaticAnalysisCache
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.config import Language, NodeType
from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import (
    ClusterCache,
    ClusterGroup,
    ClusterResult,
    ClusterScopeResult,
)
from static_analyzer.clustering.method_cluster_paths import MethodClusterPaths
from static_analyzer.clustering.delta import LanguageDelta
from static_analyzer.clustering.exceptions import IncrementalCacheMissingError
from static_analyzer.clustering.service import ClusteringService
from static_analyzer.node import Node


class TestComponentJson(unittest.TestCase):
    def test_component_json_creation(self):
        # Test creating a ComponentJson instance
        comp = ComponentJson(
            name="TestComponent",
            component_id="test_comp_id",
            description="Test description",
            can_expand=True,
            file_methods=[
                ComponentFileMethodGroupJson(file_path="file1.py", methods=[]),
                ComponentFileMethodGroupJson(file_path="file2.py", methods=[]),
            ],
            key_entities=[],
        )

        self.assertEqual(comp.name, "TestComponent")
        self.assertEqual(comp.description, "Test description")
        self.assertTrue(comp.can_expand)
        self.assertEqual([fg.file_path for fg in comp.file_methods], ["file1.py", "file2.py"])

    def test_component_json_defaults(self):
        # Test default values
        comp = ComponentJson(
            name="Component",
            component_id="comp_defaults_id",
            description="Description",
            key_entities=[],
        )

        self.assertFalse(comp.can_expand)
        self.assertEqual(comp.file_methods, [])

    def test_component_json_with_references(self):
        # Test with source code references
        ref = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file="test.py",
            reference_start_line=1,
            reference_end_line=10,
        )
        comp = ComponentJson(
            name="Component",
            component_id="comp_ref_id",
            description="Description",
            key_entities=[ref],
        )

        self.assertEqual(len(comp.key_entities), 1)
        self.assertEqual(comp.key_entities[0].qualified_name, "test.TestClass")


class TestUnifiedAnalysisJson(unittest.TestCase):
    def test_unified_analysis_json_creation(self):
        # Test creating a UnifiedAnalysisJson instance
        from diagram_analysis.analysis_json import AnalysisMetadata

        comp1 = ComponentJson(
            name="Comp1",
            component_id="comp1_id",
            description="Description 1",
            key_entities=[],
        )
        comp2 = ComponentJson(
            name="Comp2",
            component_id="comp2_id",
            description="Description 2",
            key_entities=[],
        )
        rel = RelationJson(src_name="Comp1", dst_name="Comp2", relation="uses")

        analysis = UnifiedAnalysisJson(
            metadata=AnalysisMetadata(
                generated_at="2026-01-01T00:00:00Z", repo_name="test", depth_level=1, depth_cap=1
            ),
            description="Test analysis",
            components=[comp1, comp2],
            components_relations=[rel],
        )

        self.assertEqual(analysis.description, "Test analysis")
        self.assertEqual(len(analysis.components), 2)
        self.assertEqual(len(analysis.components_relations), 1)
        self.assertEqual(analysis.metadata.repo_name, "test")

    def test_unified_analysis_json_model_dump(self):
        # Test serialization
        from diagram_analysis.analysis_json import AnalysisMetadata

        comp = ComponentJson(
            name="Comp",
            component_id="comp_dump_id",
            description="Description",
            key_entities=[],
        )
        analysis = UnifiedAnalysisJson(
            metadata=AnalysisMetadata(
                generated_at="2026-01-01T00:00:00Z", repo_name="test", depth_level=1, depth_cap=1
            ),
            description="Test",
            components=[comp],
            components_relations=[],
        )

        data = analysis.model_dump()
        self.assertEqual(data["description"], "Test")
        self.assertEqual(len(data["components"]), 1)


class TestAnalysisJsonConversion(unittest.TestCase):
    def setUp(self):
        self.repo_dir = Path(".")

        # Create sample components
        self.comp1 = Component(
            name="Component1",
            description="First component",
            key_entities=[],
            file_methods=[FileMethodGroup(file_path="file1.py")],
        )
        self.comp2 = Component(
            name="Component2",
            description="Second component",
            key_entities=[],
            file_methods=[FileMethodGroup(file_path="file2.py")],
        )

        # Create sample relation
        self.rel = Relation(src_name="Component1", dst_name="Component2", relation="depends on")

        # Create sample analysis
        self.analysis = AnalysisInsights(
            description="Test application",
            components=[self.comp1, self.comp2],
            components_relations=[self.rel],
        )
        assign_component_ids(self.analysis)

    def _add_edge_methods_to_index(self) -> None:
        self.analysis.files = {
            "component1.py": FileEntry(
                methods=[
                    MethodEntry(
                        qualified_name="component1.run",
                        start_line=10,
                        end_line=20,
                        node_type="FUNCTION",
                    ),
                    MethodEntry(
                        qualified_name="component1.dispatch",
                        start_line=10,
                        end_line=20,
                        node_type="FUNCTION",
                    ),
                ]
            ),
            "component2.py": FileEntry(
                methods=[
                    MethodEntry(
                        qualified_name="component2.load",
                        start_line=30,
                        end_line=40,
                        node_type="FUNCTION",
                    ),
                    MethodEntry(
                        qualified_name="component2.registry",
                        start_line=30,
                        end_line=40,
                        node_type="FUNCTION",
                    ),
                ]
            ),
        }

    def test_from_component_to_json_component_can_expand_true(self):
        # Test when component can be expanded
        new_components = [self.comp1]  # comp1 can be expanded

        result = from_component_to_json_component(self.comp1, new_components, self.repo_dir)

        self.assertIsInstance(result, ComponentJson)
        self.assertEqual(result.name, "Component1")
        self.assertTrue(result.can_expand)

    def test_from_component_to_json_component_can_expand_false(self):
        # Test when component cannot be expanded
        new_components: list[Component] = []  # No new components

        result = from_component_to_json_component(self.comp1, new_components, self.repo_dir)

        self.assertIsInstance(result, ComponentJson)
        self.assertEqual(result.name, "Component1")
        self.assertFalse(result.can_expand)

    def test_from_component_to_json_component_preserves_data(self):
        # Test that all data is preserved
        ref = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file="test.py",
            reference_start_line=5,
            reference_end_line=15,
        )
        comp = Component(
            name="TestComp",
            description="Test description",
            file_methods=[
                FileMethodGroup(file_path="a.py"),
                FileMethodGroup(file_path="b.py"),
            ],
            key_entities=[ref],
        )

        result = from_component_to_json_component(comp, [], self.repo_dir)

        self.assertEqual(result.name, "TestComp")
        self.assertEqual(result.description, "Test description")
        self.assertEqual(set(fg.file_path for fg in result.file_methods), {"a.py", "b.py"})
        self.assertEqual(len(result.key_entities), 1)

    def test_key_entity_reference_file_is_relativized(self):
        # The reference resolver leaves reference_file absolute; serialization must make it
        # repo-relative like every other path, or consumers cannot match it against the tree.
        repo = Path("/tmp/some/repo")
        comp = Component(
            name="C",
            description="",
            component_id="1",
            key_entities=[
                SourceCodeReference(
                    qualified_name="pkg.mod.fn",
                    reference_file="/tmp/some/repo/pkg/mod.py",
                    reference_start_line=1,
                    reference_end_line=2,
                ),
                SourceCodeReference(
                    qualified_name="pkg.other.fn",
                    reference_file="pkg/other.py",
                    reference_start_line=1,
                    reference_end_line=2,
                ),
            ],
        )

        result = from_component_to_json_component(comp, [], repo)

        self.assertEqual(
            [ke.reference_file for ke in result.key_entities],
            ["pkg/mod.py", "pkg/other.py"],
        )

    def test_from_analysis_to_json(self):
        # Test full analysis conversion to JSON
        new_components = [self.comp1]  # Only comp1 can expand

        json_str = from_analysis_to_json(self.analysis, new_components, self.repo_dir)

        # Parse JSON to verify it's valid
        data = json.loads(json_str)

        self.assertEqual(data["description"], "Test application")
        self.assertEqual(len(data["components"]), 2)
        self.assertEqual(len(data["components_relations"]), 1)

        # Verify can_expand flags
        comp1_data = next(c for c in data["components"] if c["name"] == "Component1")
        comp2_data = next(c for c in data["components"] if c["name"] == "Component2")

        self.assertTrue(comp1_data["can_expand"])
        self.assertFalse(comp2_data["can_expand"])
        self.assertEqual(comp1_data["components"], [])
        self.assertEqual(comp1_data["components_relations"], [])
        self.assertEqual(comp2_data["components"], [])
        self.assertEqual(comp2_data["components_relations"], [])

    def test_from_analysis_to_json_includes_all_edges(self):
        self._add_edge_methods_to_index()
        self.analysis.components_relations = [
            Relation(
                src_name="Component1",
                dst_name="Component2",
                relation="calls",
                src_id="1",
                dst_id="2",
                is_static=True,
                all_edges=[
                    RelationEdge(
                        source=SourceCodeReference(
                            qualified_name="component1.run",
                            reference_file="component1.py",
                            reference_start_line=10,
                            reference_end_line=20,
                        ),
                        target=SourceCodeReference(
                            qualified_name="component2.load",
                            reference_file="component2.py",
                            reference_start_line=30,
                            reference_end_line=40,
                        ),
                        call_sites=[RelationCallSite(line=12, column=8), RelationCallSite(line=18, column=12)],
                    )
                ],
            )
        ]

        data = json.loads(from_analysis_to_json(self.analysis, [], self.repo_dir))

        relation = data["components_relations"][0]
        self.assertNotIn("edge_count", relation)
        self.assertTrue(relation["is_static"])
        self.assertEqual(relation["all_edges"][0]["source"], "component1.py|component1.run")
        self.assertEqual(relation["all_edges"][0]["target"], "component2.py|component2.load")
        self.assertEqual(
            relation["all_edges"][0]["call_sites"], [{"line": 12, "column": 8}, {"line": 18, "column": 12}]
        )

    def test_from_analysis_to_json_collapses_relations_but_keeps_edges(self):
        self._add_edge_methods_to_index()
        self.analysis.components_relations = [
            Relation(
                src_name="Component1",
                dst_name="Component2",
                relation="calls",
                src_id="1",
                dst_id="2",
                is_static=True,
                all_edges=[
                    RelationEdge(
                        source=SourceCodeReference(
                            qualified_name="component1.run",
                            reference_file="component1.py",
                            reference_start_line=10,
                            reference_end_line=20,
                        ),
                        target=SourceCodeReference(
                            qualified_name="component2.load",
                            reference_file="component2.py",
                            reference_start_line=30,
                            reference_end_line=40,
                        ),
                        call_sites=[RelationCallSite(line=12, column=8)],
                    )
                ],
            ),
            Relation(
                src_name="Component1",
                dst_name="Component2",
                relation="dispatches to",
                src_id="1",
                dst_id="2",
                key_edges=[
                    RelationEdge(
                        source=SourceCodeReference(
                            qualified_name="component1.dispatch",
                            reference_file="component1.py",
                            reference_start_line=10,
                            reference_end_line=20,
                        ),
                        target=SourceCodeReference(
                            qualified_name="component2.registry",
                            reference_file="component2.py",
                            reference_start_line=30,
                            reference_end_line=40,
                        ),
                        call_sites=[RelationCallSite(line=14, column=6)],
                    )
                ],
            ),
        ]

        data = json.loads(from_analysis_to_json(self.analysis, [], self.repo_dir))

        self.assertEqual(len(data["components_relations"]), 2)
        relations_by_label = {relation["relation"]: relation for relation in data["components_relations"]}
        self.assertEqual(len(relations_by_label["calls"]["all_edges"]), 1)
        self.assertEqual(relations_by_label["calls"]["all_edges"][0]["source"], "component1.py|component1.run")
        self.assertEqual(len(relations_by_label["dispatches to"]["key_edges"]), 1)
        self.assertEqual(
            relations_by_label["dispatches to"]["key_edges"][0]["source"], "component1.py|component1.dispatch"
        )

    def test_unified_analysis_parse_preserves_all_edges(self):
        self._add_edge_methods_to_index()
        self.analysis.components_relations = [
            Relation(
                src_name="Component1",
                dst_name="Component2",
                relation="calls",
                src_id="1",
                dst_id="2",
                is_static=True,
                all_edges=[
                    RelationEdge(
                        source=SourceCodeReference(
                            qualified_name="component1.run",
                            reference_file="component1.py",
                            reference_start_line=10,
                            reference_end_line=20,
                        ),
                        target=SourceCodeReference(
                            qualified_name="component2.load",
                            reference_file="component2.py",
                            reference_start_line=30,
                            reference_end_line=40,
                        ),
                        call_sites=[RelationCallSite(line=12, column=8), RelationCallSite(line=18, column=12)],
                    )
                ],
            )
        ]

        data = json.loads(
            build_unified_analysis_json(
                self.analysis, [], "repo", repo_dir=self.repo_dir, source_tree_hash="", depth_cap=1
            )
        )
        parsed, _ = parse_unified_analysis(data)

        relation = parsed.components_relations[0]
        self.assertTrue(relation.is_static)
        self.assertEqual(relation.all_edges[0].source.qualified_name, "component1.run")
        self.assertEqual(relation.all_edges[0].target.reference_file, "component2.py")
        self.assertEqual(
            [site.model_dump() for site in relation.all_edges[0].call_sites],
            [{"line": 12, "column": 8}, {"line": 18, "column": 12}],
        )

    def test_unified_analysis_parse_preserves_key_edges(self):
        self._add_edge_methods_to_index()
        self.analysis.components_relations = [
            Relation(
                src_name="Component1",
                dst_name="Component2",
                relation="dispatches to",
                evidence="Runtime registry dispatch",
                key_edges=[
                    RelationEdge(
                        source=SourceCodeReference(
                            qualified_name="component1.dispatch",
                            reference_file="component1.py",
                            reference_start_line=10,
                            reference_end_line=20,
                        ),
                        target=SourceCodeReference(
                            qualified_name="component2.registry",
                            reference_file="component2.py",
                            reference_start_line=30,
                            reference_end_line=40,
                        ),
                        description="dispatches through registry",
                        call_sites=[RelationCallSite(line=14, column=6), RelationCallSite(line=16, column=10)],
                    )
                ],
            )
        ]

        data = json.loads(
            build_unified_analysis_json(
                self.analysis, [], "repo", repo_dir=self.repo_dir, source_tree_hash="", depth_cap=1
            )
        )
        parsed, _ = parse_unified_analysis(data)

        edge = parsed.components_relations[0].key_edges[0]
        self.assertEqual(edge.source.qualified_name, "component1.dispatch")
        self.assertEqual(edge.target.reference_file, "component2.py")
        self.assertEqual(edge.description, "dispatches through registry")
        self.assertEqual(
            [site.model_dump() for site in edge.call_sites], [{"line": 14, "column": 6}, {"line": 16, "column": 10}]
        )

    def test_unified_analysis_parse_recovers_edges_missing_from_methods_index(self):
        data = json.loads(
            build_unified_analysis_json(
                self.analysis, [], "repo", repo_dir=self.repo_dir, source_tree_hash="", depth_cap=1
            )
        )
        data["components_relations"] = [
            {
                "relation": "calls",
                "src_name": "Component1",
                "dst_name": "Component2",
                "src_id": "1",
                "dst_id": "2",
                "is_static": True,
                "key_edges": [
                    {
                        "source": "missing.py|missing.call",
                        "target": "component2.py|component2.load",
                        "description": "external or stale endpoint",
                    }
                ],
                "all_edges": [],
            }
        ]

        parsed, _ = parse_unified_analysis(data)

        self.assertEqual(len(parsed.components_relations), 1)
        edge = parsed.components_relations[0].key_edges[0]
        self.assertEqual(edge.source.qualified_name, "missing.call")
        self.assertEqual(edge.source.reference_file, "missing.py")
        self.assertEqual(edge.target.qualified_name, "component2.load")
        self.assertEqual(edge.target.reference_file, "component2.py")

    def test_unified_analysis_does_not_invent_kinds_for_endpoints_outside_files(self):
        self.analysis.components_relations = [
            Relation(
                relation="registers",
                src_name="Component1",
                dst_name="Component2",
                src_id="1",
                dst_id="2",
                key_edges=[
                    RelationEdge(
                        source=SourceCodeReference(qualified_name="importlib.metadata.entry_points"),
                        target=SourceCodeReference(
                            qualified_name="plugin.register",
                            reference_file="plugin.py",
                            reference_start_line=12,
                            reference_end_line=18,
                        ),
                    )
                ],
            )
        ]
        index_relation_endpoints(self.analysis, self.repo_dir)

        data = json.loads(
            build_unified_analysis_json(
                self.analysis, [], "repo", repo_dir=self.repo_dir, source_tree_hash="", depth_cap=1
            )
        )

        self.assertNotIn("|importlib.metadata.entry_points", data["methods_index"])
        self.assertNotIn("plugin.py|plugin.register", data["methods_index"])
        self.assertNotIn("", data["files"])
        parsed, _ = parse_unified_analysis(data)
        edge = parsed.components_relations[0].key_edges[0]
        self.assertEqual(edge.source.qualified_name, "importlib.metadata.entry_points")
        self.assertIsNone(edge.source.reference_file)
        self.assertEqual(edge.target.reference_file, "plugin.py")
        self.assertNotIn("", parsed.files)

    def test_source_tree_hash_written_to_metadata(self):
        # The precomputed hash the caller passes is what lands in metadata — the
        # builder no longer re-walks the tree to recompute it.
        precomputed = "a1b2c3d4e5f60718"
        data = json.loads(
            build_unified_analysis_json(
                self.analysis, [], "repo", repo_dir=self.repo_dir, source_tree_hash=precomputed, depth_cap=1
            )
        )
        self.assertEqual(data["metadata"]["source_tree_hash"], precomputed)

    def test_from_analysis_to_json_does_not_infer_unproven_key_edge_call_sites(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = Path(tmp_dir) / "component1.py"
            source_file.write_text(
                "def dispatch(flag):\n"
                "    if flag:\n"
                "        load()\n"
                "    else:\n"
                "        load()\n"
                "\n"
                "def load():\n"
                "    pass\n"
            )
            source_path = str(source_file)
            target_path = str(source_file)
            self.analysis.files = {
                source_path: FileEntry(
                    methods=[
                        MethodEntry(
                            qualified_name="component1.dispatch",
                            start_line=1,
                            end_line=5,
                            node_type="FUNCTION",
                        ),
                        MethodEntry(
                            qualified_name="component1.load",
                            start_line=7,
                            end_line=8,
                            node_type="FUNCTION",
                        ),
                    ]
                )
            }
            self.analysis.components_relations = [
                Relation(
                    src_name="Component1",
                    dst_name="Component2",
                    relation="dispatches to",
                    key_edges=[
                        RelationEdge(
                            source=SourceCodeReference(
                                qualified_name="component1.dispatch",
                                reference_file=source_path,
                                reference_start_line=1,
                                reference_end_line=5,
                            ),
                            target=SourceCodeReference(
                                qualified_name="component1.load",
                                reference_file=target_path,
                                reference_start_line=7,
                                reference_end_line=8,
                            ),
                        )
                    ],
                )
            ]

            data = json.loads(from_analysis_to_json(self.analysis, [], self.repo_dir))

        key_edge = data["components_relations"][0]["key_edges"][0]
        self.assertEqual(key_edge["call_sites"], [])

    def test_from_analysis_to_json_normalizes_absolute_relation_edge_paths(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_dir = Path(tmp_dir)
            source_file = repo_dir / "component1.py"
            source_file.write_text("def run():\n    load()\n\ndef load():\n    pass\n")
            self.analysis.files = {
                "component1.py": FileEntry(
                    methods=[
                        MethodEntry(qualified_name="component1.run", start_line=1, end_line=2, node_type="FUNCTION"),
                        MethodEntry(qualified_name="component1.load", start_line=4, end_line=5, node_type="FUNCTION"),
                    ]
                )
            }
            self.analysis.components_relations = [
                Relation(
                    src_name="Component1",
                    dst_name="Component2",
                    relation="calls",
                    key_edges=[
                        RelationEdge(
                            source=SourceCodeReference(
                                qualified_name="component1.run",
                                reference_file=str(source_file),
                                reference_start_line=1,
                                reference_end_line=2,
                            ),
                            target=SourceCodeReference(
                                qualified_name="component1.load",
                                reference_file=str(source_file),
                                reference_start_line=4,
                                reference_end_line=5,
                            ),
                        )
                    ],
                )
            ]

            data = json.loads(from_analysis_to_json(self.analysis, [], repo_dir=repo_dir))

        key_edge = data["components_relations"][0]["key_edges"][0]
        self.assertEqual(key_edge["source"], "component1.py|component1.run")
        self.assertEqual(key_edge["target"], "component1.py|component1.load")
        self.assertEqual(key_edge["call_sites"], [])

    def test_from_analysis_to_json_empty(self):
        # Test with empty analysis
        empty_analysis = AnalysisInsights(description="Empty", components=[], components_relations=[])

        json_str = from_analysis_to_json(empty_analysis, [], self.repo_dir)

        data = json.loads(json_str)
        self.assertEqual(data["description"], "Empty")
        self.assertEqual(len(data["components"]), 0)
        self.assertEqual(len(data["components_relations"]), 0)

    def test_from_analysis_to_json_with_references(self):
        # Test with source code references
        ref1 = SourceCodeReference(
            qualified_name="src.class1.Class1",
            reference_file="src/class1.py",
            reference_start_line=10,
            reference_end_line=20,
        )
        ref2 = SourceCodeReference(
            qualified_name="src.class2.Class2",
            reference_file="src/class2.py",
            reference_start_line=5,
            reference_end_line=15,
        )

        comp = Component(
            name="WithRefs",
            description="Component with references",
            key_entities=[ref1, ref2],
        )

        analysis = AnalysisInsights(description="Test", components=[comp], components_relations=[])

        json_str = from_analysis_to_json(analysis, [], self.repo_dir)
        data = json.loads(json_str)

        comp_data = data["components"][0]
        self.assertEqual(len(comp_data["key_entities"]), 2)

    def test_from_analysis_to_json_formatting(self):
        # Test that JSON is properly formatted with indentation
        json_str = from_analysis_to_json(self.analysis, [], self.repo_dir)

        # Check that it's indented (contains newlines and spaces)
        self.assertIn("\n", json_str)
        self.assertIn("  ", json_str)  # 2-space indentation


class TestDepthCapPersistence(unittest.TestCase):
    """depth_cap must never be saved lower than the tree's own realized depth —
    a partial run against a shallow baseline that grafts on a deeper subtree
    would otherwise permanently freeze future incremental/partial runs at the
    old, shallower cap even though the tree has already gone deeper."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output_dir = Path(self.temp_dir)
        self.repo_dir = Path(".")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_root(self) -> AnalysisInsights:
        comp = Component(
            name="Root",
            component_id="1",
            description="Root component",
            key_entities=[],
            file_methods=[FileMethodGroup(file_path="file1.py")],
        )
        return AnalysisInsights(description="Test", components=[comp], components_relations=[])

    def test_save_clamps_depth_cap_to_realized_depth(self):
        # A depth-1 baseline (no sub-analyses) with a low configured cap.
        save_analysis(
            analysis=self._make_root(),
            output_dir=self.output_dir,
            repo_dir=self.repo_dir,
            source_tree_hash="hash1",
            repo_name="test",
            depth_cap=1,
        )
        baseline_metadata = load_analysis_metadata(self.output_dir)
        assert baseline_metadata is not None
        self.assertEqual(baseline_metadata["depth_cap"], 1)

        # Partial run grafts a depth-2 subtree onto the root (component "1" now
        # has sub_analyses["1"]), but is still constructed with the old cap (1)
        # — saving must not persist depth_cap=1 now that the realized tree is
        # 2 levels deep.
        child_analysis = AnalysisInsights(description="Child", components=[], components_relations=[])
        save_analysis(
            analysis=self._make_root(),
            output_dir=self.output_dir,
            repo_dir=self.repo_dir,
            source_tree_hash="hash2",
            repo_name="test",
            sub_analyses={"1": child_analysis},
            depth_cap=1,
        )
        metadata = load_analysis_metadata(self.output_dir)
        assert metadata is not None
        self.assertEqual(metadata["depth_level"], 2)
        self.assertEqual(metadata["depth_cap"], 2)

    def test_save_preserves_higher_cap_than_realized_depth(self):
        # A shallow realized tree with a cap that's already deeper (separability
        # decided there was nothing more to split) must keep the higher cap.
        save_analysis(
            analysis=self._make_root(),
            output_dir=self.output_dir,
            repo_dir=self.repo_dir,
            source_tree_hash="hash1",
            repo_name="test",
            depth_cap=3,
        )
        metadata = load_analysis_metadata(self.output_dir)
        assert metadata is not None
        self.assertEqual(metadata["depth_level"], 1)
        self.assertEqual(metadata["depth_cap"], 3)


class TestDiagramGenerator(unittest.TestCase):
    def setUp(self):
        # Create temporary directories for testing
        self.temp_dir = tempfile.mkdtemp()
        self.repo_location = Path(self.temp_dir) / "test_repo"
        self.repo_location.mkdir(parents=True)
        self.temp_folder = Path(self.temp_dir) / "temp"
        self.temp_folder.mkdir(parents=True)
        self.output_dir = Path(self.temp_dir) / "output"
        self.output_dir.mkdir(parents=True)

        # Create a simple test file
        (self.repo_location / "test.py").write_text("def test(): pass")

    def tearDown(self):
        # Clean up temporary directory
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init(self):
        # Test DiagramGenerator initialization
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )

        self.assertEqual(gen.repo_location, self.repo_location)
        self.assertEqual(gen.repo_name, "test_repo")
        self.assertEqual(gen.output_dir, self.output_dir)
        self.assertEqual(gen.depth_level, 2)
        self.assertIsNone(gen.details_agent)
        self.assertIsNone(gen.abstraction_agent)
        self.assertIsNone(gen.incremental_agent)

    @patch("diagram_analysis.diagram_generator.get_static_analysis")
    def test_new_analyzer_honors_cache_reuse_override(self, mock_get_static_analysis):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        gen.source_sha = "current-sha"
        mock_get_static_analysis.return_value = MagicMock(spec=StaticAnalysisResults)

        with patch.dict(os.environ, {"CODEBOARDING_DISABLE_CACHE_REUSE": "true"}):
            gen._get_static_with_new_analyzer()

        self.assertTrue(mock_get_static_analysis.call_args.kwargs["skip_cache"])

    @patch("diagram_analysis.diagram_generator.ProjectScanner")
    @patch("diagram_analysis.diagram_generator.get_static_analysis")
    @patch("diagram_analysis.diagram_generator.initialize_llms")
    @patch("diagram_analysis.diagram_generator.MetaAgent")
    @patch("diagram_analysis.diagram_generator.DetailsAgent")
    @patch("diagram_analysis.diagram_generator.AbstractionAgent")
    def test_prepare_analysis(
        self,
        mock_abstraction,
        mock_details,
        mock_meta,
        mock_initialize_llms,
        mock_get_static_analysis,
        mock_scanner,
    ):
        # Test prepare_analysis method
        # Return a proper StaticAnalysisResults object
        mock_analysis_results = StaticAnalysisResults()
        mock_analysis_results.diagnostics = {}
        mock_get_static_analysis.return_value = mock_analysis_results

        # Mock LLM initialization
        mock_agent_llm = Mock()
        mock_parsing_llm = Mock()
        mock_initialize_llms.return_value = (mock_agent_llm, mock_parsing_llm)

        mock_meta_instance = Mock()
        mock_meta_instance.analyze_project_metadata.return_value = {"meta": "context"}
        mock_meta_instance.agent_monitoring_callback = Mock(model_name=None)
        mock_meta.return_value = mock_meta_instance

        mock_details_instance = Mock()
        mock_details_instance.agent_monitoring_callback = Mock(model_name=None)
        mock_details.return_value = mock_details_instance

        mock_abstraction_instance = Mock()
        mock_abstraction_instance.agent_monitoring_callback = Mock(model_name=None)
        mock_abstraction.return_value = mock_abstraction_instance

        # Mock ProjectScanner to avoid tokei dependency
        mock_scanner_instance = Mock()
        mock_scanner_instance.scan.return_value = []
        mock_scanner_instance.all_text_files = []
        mock_scanner.return_value = mock_scanner_instance

        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )

        hierarchy = ClusterScopeResult(scope_id="root")
        with (
            patch("diagram_analysis.diagram_generator.IncrementalAgent") as mock_incremental,
            patch(
                "static_analyzer.clustering.service.ClusteringService.build_full_hierarchy", return_value=hierarchy
            ) as mock_build_hierarchy,
        ):
            gen.prepare_analysis()

        # Verify agents were created
        mock_build_hierarchy.assert_called_once_with(mock_analysis_results, 2, {})
        self.assertIs(gen.clustering_hierarchy, hierarchy)
        self.assertIsNotNone(gen.meta_agent)
        self.assertIsNotNone(gen.details_agent)
        self.assertIsNotNone(gen.abstraction_agent)
        self.assertIs(gen.incremental_agent, mock_incremental.return_value)
        mock_meta_instance.analyze_project_metadata.assert_called_once_with(skip_cache=False)

    def test_prepare_analysis_overlaps_metadata_with_deterministic_analysis(self):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        metadata_started = Event()
        deterministic_finished = Event()
        meta_context = MagicMock()
        meta_agent = Mock()

        def analyze_metadata(**_kwargs):
            metadata_started.set()
            self.assertTrue(deterministic_finished.wait(timeout=1))
            return meta_context

        def deterministic_analysis(**_kwargs):
            self.assertTrue(metadata_started.wait(timeout=1))
            deterministic_finished.set()

        meta_agent.analyze_project_metadata.side_effect = analyze_metadata
        gen.deterministic_analysis = Mock(side_effect=deterministic_analysis)
        gen._initialize_meta_agent = Mock(side_effect=lambda *_args: setattr(gen, "meta_agent", meta_agent))
        gen._complete_agent_initialization = Mock()

        with patch("diagram_analysis.diagram_generator.initialize_llms", return_value=(Mock(), Mock())):
            gen.prepare_analysis(hierarchy_depth=3)

        gen.deterministic_analysis.assert_called_once_with(
            hierarchy_depth=3,
            target_component=None,
            persisted_scopes={},
        )
        gen._complete_agent_initialization.assert_called_once()

    def test_prepare_analysis_skips_llms_for_empty_incremental_delta(self):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        preparation = MagicMock()
        preparation.has_changes = False
        gen.deterministic_analysis = Mock(
            side_effect=lambda **_kwargs: setattr(gen, "_incremental_preparation", preparation)
        )
        gen.agent_init = Mock()

        with patch("diagram_analysis.diagram_generator.initialize_llms") as initialize_llms:
            gen.prepare_analysis(incremental=True)

        initialize_llms.assert_not_called()
        gen.agent_init.assert_not_called()

    def test_prepare_analysis_initializes_agents_for_group_membership_change(self):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        delta = MagicMock()
        delta.has_changes = False
        preparation = _IncrementalPreparation(
            delta=delta,
            baseline_membership=_MembershipBaseline(),
            has_membership_changes=True,
        )
        gen.deterministic_analysis = Mock(
            side_effect=lambda **_kwargs: setattr(gen, "_incremental_preparation", preparation)
        )
        gen.agent_init = Mock()

        gen.prepare_analysis(incremental=True)

        gen.agent_init.assert_called_once()

    def test_deterministic_analysis_rejects_missing_incremental_baseline_before_clustering(self):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        gen._get_static_with_new_analyzer = Mock(return_value=StaticAnalysisResults())

        root = AnalysisInsights(description="root", components=[], components_relations=[])
        with patch.object(ClusteringService, "build_incremental_hierarchy") as mock_build_hierarchy:
            with self.assertRaises(IncrementalCacheMissingError):
                gen.deterministic_analysis(
                    incremental=True,
                    persisted_scopes={ROOT_SCOPE_ID: root},
                )

        mock_build_hierarchy.assert_not_called()

    def test_process_component_with_exception(self):
        # Test processing a component that raises an exception

        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )

        # Setup agents
        gen.details_agent = Mock()

        # Mock to raise exception
        gen.details_agent.run.side_effect = Exception("Test error")

        # Create test component
        component = Component(name="TestComponent", description="Test", key_entities=[], component_id="1")
        scope = ClusterScopeResult(scope_id="1")
        gen.clustering_hierarchy = ClusterScopeResult(
            scope_id="root",
            groups=[ClusterGroup(group_id="1", cluster_ids=[1], children=scope)],
        )

        result_name, result_analysis, new_components = gen.process_component(component)

        # Should return None and empty list on exception
        self.assertIsNone(result_name)
        self.assertIsNone(result_analysis)
        self.assertEqual(new_components, [])

    def test_process_component_propagates_missing_scope(self):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        gen.details_agent = Mock()
        gen.clustering_hierarchy = ClusterScopeResult(scope_id="root")
        component = Component(name="Missing", description="", key_entities=[], component_id="4")

        with self.assertRaisesRegex(ClusteringScopeUnavailableError, "no precomputed scope"):
            gen.process_component(component)

    def test_build_component_scope_propagates_missing_cfg_ownership(self):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        gen.static_analysis = StaticAnalysisResults()
        component = Component(name="Missing", description="", key_entities=[], component_id="4")

        with self.assertRaisesRegex(ClusteringScopeUnavailableError, "no owned CFG nodes"):
            gen._build_component_scope(component, hierarchy_depth=3)

    @patch("diagram_analysis.diagram_generator.get_expandable_components")
    def test_hierarchy_component_traverses_the_preclustered_scope(self, mock_get_expandable_components):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=3,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        component = Component(name="Root", description="", key_entities=[], component_id="1")
        child = Component(name="Child", description="", key_entities=[], component_id="1.1")
        child_analysis = AnalysisInsights(description="child", components=[child], components_relations=[])
        grandchild_scope = ClusterScopeResult(scope_id="1.1")
        scope = ClusterScopeResult(
            scope_id="1",
            groups=[ClusterGroup(group_id="1.1", cluster_ids=[1], children=grandchild_scope)],
        )
        gen.clustering_hierarchy = ClusterScopeResult(
            scope_id="root",
            groups=[ClusterGroup(group_id="1", cluster_ids=[1], children=scope)],
        )
        gen.details_agent = Mock()
        gen.details_agent.run.return_value = (child_analysis, scope.leaf_clusters_by_language)

        component_id, result, new_components = gen._process_component(component)

        self.assertEqual(component_id, "1")
        self.assertIs(result, child_analysis)
        self.assertEqual(new_components, [child])
        gen.details_agent.run.assert_called_once_with(scope, component)
        mock_get_expandable_components.assert_not_called()

    def test_build_component_scope_uses_persisted_absorbed_id(self):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=3,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        graph = CallGraph(language="python")
        graph.add_node(Node("pkg.persisted.run", NodeType.FUNCTION, str(self.repo_location / "persisted.py"), 1, 4))
        graph.add_node(Node("pkg.other.run", NodeType.FUNCTION, str(self.repo_location / "other.py"), 1, 4))
        gen.static_analysis = StaticAnalysisResults()
        gen.static_analysis.add_cfg(Language.PYTHON, graph)
        component = Component(
            name="Persisted child",
            description="",
            key_entities=[],
            component_id="2",
            file_methods=[
                FileMethodGroup(
                    file_path="persisted.py",
                    methods=[
                        MethodEntry(
                            qualified_name="pkg.persisted.run",
                            start_line=1,
                            end_line=4,
                            node_type="FUNCTION",
                        )
                    ],
                )
            ],
        )

        scope = gen._build_component_scope(component, hierarchy_depth=4)

        self.assertEqual(scope.scope_id, "2")
        self.assertEqual(set(scope.graphs_by_language["python"].nodes), {"pkg.persisted.run"})
        self.assertTrue(all(group.group_id.startswith("2.") for group in scope.groups))

    @patch("diagram_analysis.diagram_generator.get_expandable_components")
    def test_preclustered_expansion_flags_are_reused_when_saving(self, mock_get_expandable_components):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        expandable = Component(
            name="A",
            description="",
            key_entities=[],
            component_id="1",
            file_methods=[FileMethodGroup(file_path="a.py")],
        )
        leaf = Component(name="B", description="", key_entities=[], component_id="2")
        ignored = Component(name="Ignored", description="", key_entities=[], component_id="3")
        analysis = AnalysisInsights(
            description="root",
            components=[expandable, leaf, ignored],
            components_relations=[],
        )
        gen.details_agent = Mock()
        gen.clustering_hierarchy = ClusterScopeResult(
            scope_id="root",
            groups=[
                ClusterGroup(group_id="1", cluster_ids=[1], expandable=True),
                ClusterGroup(group_id="2", cluster_ids=[2], expandable=False),
                ClusterGroup(group_id="3", cluster_ids=[3], expandable=True),
            ],
        )

        root_ids, sub_ids = gen._expandable_ids_for_tree(analysis, {})

        self.assertEqual(root_ids, ["1"])
        self.assertEqual(sub_ids, {})
        mock_get_expandable_components.assert_not_called()

    @patch("diagram_analysis.diagram_generator.get_expandable_components")
    def test_preclustered_save_preserves_generated_subtree_owner(self, mock_get_expandable_components):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        owner = Component(name="A", description="", key_entities=[], component_id="1")
        child = Component(name="A child", description="", key_entities=[], component_id="1.1")
        root_analysis = AnalysisInsights(description="root", components=[owner], components_relations=[])
        sub_analyses = {"1": AnalysisInsights(description="child scope", components=[child], components_relations=[])}
        gen.details_agent = Mock()
        gen.clustering_hierarchy = ClusterScopeResult(scope_id="root")
        gen.clustering_hierarchy.register_scope(
            "1",
            ClusterScopeResult(
                scope_id="1",
                groups=[ClusterGroup(group_id="1.1", cluster_ids=[1], expandable=False)],
            ),
        )

        root_ids, sub_ids = gen._expandable_ids_for_tree(root_analysis, sub_analyses)

        self.assertEqual(root_ids, ["1"])
        self.assertEqual(sub_ids, {"1": []})
        mock_get_expandable_components.assert_not_called()

    @patch("diagram_analysis.diagram_generator.get_expandable_components")
    def test_partial_hierarchy_preserves_uncomputed_sibling_expansion(self, mock_get_expandable_components):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        target = Component(
            name="Target",
            description="",
            key_entities=[],
            component_id="1",
            file_methods=[FileMethodGroup(file_path="target.py")],
        )
        sibling = Component(
            name="Sibling",
            description="",
            key_entities=[],
            component_id="2",
            file_methods=[FileMethodGroup(file_path="sibling.py")],
        )
        children = [
            Component(name="Child A", description="", key_entities=[], component_id="1.1"),
            Component(name="Child B", description="", key_entities=[], component_id="1.2"),
        ]
        root_analysis = AnalysisInsights(
            description="root",
            components=[target, sibling],
            components_relations=[],
        )
        sub_analyses = {
            "1": AnalysisInsights(description="target", components=children, components_relations=[]),
        }
        gen.details_agent = Mock()
        gen.clustering_hierarchy = ClusterScopeResult(scope_id="root")
        gen.clustering_hierarchy.register_scope(
            "1",
            ClusterScopeResult(
                scope_id="1",
                groups=[
                    ClusterGroup(group_id="1.1", cluster_ids=[1], expandable=False),
                    ClusterGroup(group_id="1.2", cluster_ids=[2], expandable=False),
                ],
            ),
        )

        root_ids, sub_ids = gen._expandable_ids_for_tree(
            root_analysis,
            sub_analyses,
            preserved_expandable_ids={"1", "2"},
        )

        self.assertEqual(root_ids, ["1", "2"])
        self.assertEqual(sub_ids, {"1": []})
        mock_get_expandable_components.assert_not_called()

    def test_absorbed_ids_reroot_preclustered_expansion_flags(self):
        hierarchy = ClusterScopeResult(
            scope_id="root",
            groups=[
                ClusterGroup(
                    group_id="1",
                    cluster_ids=[1],
                    children=ClusterScopeResult(
                        scope_id="1",
                        groups=[
                            ClusterGroup(
                                group_id="1.1",
                                cluster_ids=[2],
                                children=ClusterScopeResult(
                                    scope_id="1.1",
                                    groups=[ClusterGroup(group_id="1.1.2", cluster_ids=[3], expandable=True)],
                                ),
                            )
                        ],
                    ),
                )
            ],
        )

        hierarchy.reroot_indexes(["1.1"])

        self.assertEqual(set(hierarchy.clustering_groups), {"1", "1.2"})
        self.assertTrue(hierarchy.clustering_groups["1.2"].expandable)
        self.assertEqual(hierarchy.clustering_groups["1.2"].group_id, "1.2")

    @patch("diagram_analysis.diagram_generator.get_expandable_components")
    def test_full_analysis_consumes_the_precomputed_hierarchy(self, mock_get_expandable_components):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        child_scope = ClusterScopeResult(scope_id="1")
        hierarchy = ClusterScopeResult(
            scope_id="root",
            groups=[ClusterGroup(group_id="1", cluster_ids=[1], expandable=True, children=child_scope)],
        )
        component = Component(name="Root", description="", key_entities=[], component_id="1")
        analysis = AnalysisInsights(description="root", components=[component], components_relations=[])
        gen.static_analysis = MagicMock(spec=StaticAnalysisResults)
        gen.static_analysis.available_cfgs.return_value = {}
        gen.clustering_hierarchy = hierarchy
        gen.details_agent = Mock()
        gen.abstraction_agent = Mock()
        gen.abstraction_agent.run.return_value = analysis
        gen._generate_subcomponents = Mock(return_value=([], {}))
        expected_path = self.output_dir / "analysis.json"
        gen.finalize_and_save = Mock(return_value=expected_path)

        result = gen.generate_analysis()

        self.assertEqual(result, expected_path)
        gen.abstraction_agent.run.assert_called_once_with(hierarchy)
        gen._generate_subcomponents.assert_called_once_with(analysis, [component])
        mock_get_expandable_components.assert_not_called()

    @patch("diagram_analysis.diagram_generator.save_analysis")
    def test_generate_analysis_frontier_submits_child_before_slow_sibling_finishes(self, mock_save_analysis):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=3,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )

        root_a = Component(
            name="A",
            description="Root A",
            key_entities=[],
            source_cluster_ids=["1"],
            file_methods=[FileMethodGroup(file_path="a.py")],
        )
        root_b = Component(
            name="B",
            description="Root B",
            key_entities=[],
            source_cluster_ids=["2"],
            file_methods=[FileMethodGroup(file_path="b.py")],
        )
        child_a = Component(
            name="A-child",
            description="Child of A",
            key_entities=[],
            source_cluster_ids=["3"],
            file_methods=[FileMethodGroup(file_path="a_child.py")],
        )

        root_analysis = AnalysisInsights(description="Root", components=[root_a, root_b], components_relations=[])
        sub_analysis_a = AnalysisInsights(description="A sub", components=[child_a], components_relations=[])
        sub_analysis_b = AnalysisInsights(description="B sub", components=[], components_relations=[])
        sub_analysis_child = AnalysisInsights(description="Child sub", components=[], components_relations=[])

        gen.details_agent = Mock()
        mock_save_analysis.return_value = self.output_dir / "analysis.json"

        timestamps: dict[str, float] = {}

        def process_component_side_effect(component: Component):
            if component.name == "A":
                timestamps["a_start"] = time.monotonic()
                time.sleep(0.05)
                timestamps["a_end"] = time.monotonic()
                return "A", sub_analysis_a, [child_a]
            if component.name == "B":
                timestamps["b_start"] = time.monotonic()
                time.sleep(0.35)
                timestamps["b_end"] = time.monotonic()
                return "B", sub_analysis_b, []
            if component.name == "A-child":
                timestamps["child_start"] = time.monotonic()
                return "A-child", sub_analysis_child, []
            raise AssertionError(f"Unexpected component: {component.name}")

        gen._process_component = Mock(side_effect=process_component_side_effect)

        expanded_components, sub_analyses = gen._generate_subcomponents(root_analysis, [root_a, root_b])

        self.assertEqual(set(sub_analyses), {"A", "B", "A-child"})
        self.assertEqual({component.name for component in expanded_components}, {"A", "B", "A-child"})
        self.assertIn("child_start", timestamps)
        self.assertIn("b_end", timestamps)
        self.assertLess(timestamps["child_start"], timestamps["b_end"])

        processed_names = [call.args[0].name for call in gen._process_component.call_args_list]
        self.assertIn("A-child", processed_names)

    def test_generate_analysis_uses_hierarchy_expandables_for_can_expand(self):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=1,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )

        # Prevent prepare_analysis from running.
        gen.abstraction_agent = Mock()
        gen.details_agent = Mock()
        gen.static_analysis = StaticAnalysisResults()

        comp1 = Component(
            name="Component1",
            description="First",
            key_entities=[],
            file_methods=[
                FileMethodGroup(
                    file_path="file1.py",
                    methods=[
                        MethodEntry(qualified_name="Component1.method1", start_line=1, end_line=10, node_type="METHOD"),
                        MethodEntry(
                            qualified_name="Component1.method2", start_line=11, end_line=20, node_type="METHOD"
                        ),
                    ],
                )
            ],
        )
        comp2 = Component(
            name="Component2",
            description="Second",
            key_entities=[],
            file_methods=[
                FileMethodGroup(
                    file_path="file2.py",
                    methods=[
                        MethodEntry(qualified_name="Component2.method1", start_line=1, end_line=10, node_type="METHOD"),
                        MethodEntry(
                            qualified_name="Component2.method2", start_line=11, end_line=20, node_type="METHOD"
                        ),
                    ],
                )
            ],
        )
        analysis = AnalysisInsights(
            description="Test analysis",
            components=[comp1, comp2],
            components_relations=[],
        )
        assign_component_ids(analysis)

        hierarchy = ClusterScopeResult(
            scope_id="root",
            groups=[
                ClusterGroup(group_id="1", cluster_ids=[1], expandable=True),
                ClusterGroup(group_id="2", cluster_ids=[2], expandable=False),
            ],
        )
        gen.clustering_hierarchy = hierarchy
        gen.abstraction_agent.run.return_value = analysis
        captured: dict[str, list[Component]] = {}

        def _capture_build(
            *,
            analysis,
            expandable_components,
            repo_name,
            repo_dir,
            source_tree_hash,
            depth_cap,
            sub_analyses,
            file_coverage_summary,
        ):
            captured["expandable_components"] = expandable_components
            return "{}"

        with patch(
            "diagram_analysis.io_utils.build_unified_analysis_json",
            side_effect=_capture_build,
        ):
            gen.generate_analysis()

        # The deterministic hierarchy is authoritative: component "2" was kept as a leaf.
        self.assertEqual(
            sorted([c.component_id for c in captured["expandable_components"]]),
            [comp1.component_id],
        )

    def test_generate_analysis_depth_one_preserves_root_expandable_flags(self):
        comp1 = Component(
            name="Comp1",
            description="Component one",
            key_entities=[],
            file_methods=[
                FileMethodGroup(
                    file_path="a.py",
                    methods=[
                        MethodEntry(qualified_name="Comp1.method1", start_line=1, end_line=10, node_type="METHOD"),
                        MethodEntry(qualified_name="Comp1.method2", start_line=11, end_line=20, node_type="METHOD"),
                    ],
                )
            ],
        )
        comp2 = Component(
            name="Comp2",
            description="Component two",
            key_entities=[],
            file_methods=[
                FileMethodGroup(
                    file_path="b.py",
                    methods=[
                        MethodEntry(qualified_name="Comp2.method1", start_line=1, end_line=10, node_type="METHOD"),
                        MethodEntry(qualified_name="Comp2.method2", start_line=11, end_line=20, node_type="METHOD"),
                    ],
                )
            ],
        )
        analysis = AnalysisInsights(
            description="Root analysis",
            components=[comp1, comp2],
            components_relations=[],
        )
        assign_component_ids(analysis)

        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=1,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        gen.details_agent = Mock()
        gen.abstraction_agent = Mock()
        gen.incremental_agent = Mock()
        gen.static_analysis = StaticAnalysisResults()
        hierarchy = ClusterScopeResult(
            scope_id="root",
            groups=[
                ClusterGroup(group_id="1", cluster_ids=[1], expandable=True),
                ClusterGroup(group_id="2", cluster_ids=[2], expandable=True),
            ],
        )
        gen.clustering_hierarchy = hierarchy
        gen.abstraction_agent.run.return_value = analysis

        gen.generate_analysis()

        written = json.loads((self.output_dir / "analysis.json").read_text())
        self.assertEqual([c["can_expand"] for c in written["components"]], [True, True])

    def test_generate_analysis_incremental_raises_when_cluster_cache_missing(self):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        gen.details_agent = Mock()
        gen.abstraction_agent = Mock()
        gen.incremental_agent = Mock()
        # Empty static analysis -> snapshot has no cluster ids -> incremental
        # path must refuse rather than silently re-deriving from scratch.
        gen.static_analysis = StaticAnalysisResults()

        root_analysis = AnalysisInsights(description="root", components=[], components_relations=[])

        with self.assertRaises(IncrementalCacheMissingError) as ctx:
            gen.generate_analysis_incremental(root_analysis, {})

        self.assertEqual(ctx.exception.artifact_dir, self.output_dir)
        self.assertIn(str(self.output_dir), str(ctx.exception))

    def test_component_depth_uses_absolute_hierarchical_depth(self):
        self.assertEqual(_component_depth("1"), 1)
        self.assertEqual(_component_depth("1.1"), 2)
        self.assertEqual(_component_depth("1.1.3"), 3)
        self.assertEqual(_component_depth(None), 1)
        self.assertEqual(_component_depth(""), 1)

    def test_component_expansion_seeds_skip_components_at_max_depth(self):
        root = Component(name="Root", description="", key_entities=[], component_id="1")
        child = Component(name="Child", description="", key_entities=[], component_id="1.1")
        leaf = Component(name="Leaf", description="", key_entities=[], component_id="1.1.3")

        seeds = _component_expansion_seeds([root, child, leaf], max_depth=3)

        self.assertEqual([(component.component_id, level) for component, level in seeds], [("1", 1), ("1.1", 2)])
        self.assertEqual(_component_expansion_seeds([root, child, leaf], max_depth=1), [])

    @patch("diagram_analysis.diagram_generator.save_analysis")
    def test_generate_subcomponents_respects_absolute_depth(
        self,
        mock_save_analysis,
    ):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=3,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        gen.details_agent = Mock()

        root_analysis = AnalysisInsights(description="root", components=[], components_relations=[])
        depth_two = Component(name="Depth two", description="", key_entities=[], component_id="1.1")
        max_depth_leaf = Component(name="Leaf", description="", key_entities=[], component_id="1.1.3")
        generated_child = Component(name="Generated", description="", key_entities=[], component_id="1.1.1")
        child_analysis = AnalysisInsights(
            description="child",
            components=[generated_child],
            components_relations=[],
        )

        scope = ClusterScopeResult(scope_id="1.1")
        gen.clustering_hierarchy = ClusterScopeResult(
            scope_id="root",
            groups=[ClusterGroup(group_id="1.1", cluster_ids=[1], children=scope)],
        )
        gen.details_agent.run.return_value = (child_analysis, {})

        expanded_components, sub_analyses = gen._generate_subcomponents(root_analysis, [depth_two, max_depth_leaf])

        gen.details_agent.run.assert_called_once_with(scope, depth_two)
        self.assertEqual([component.component_id for component in expanded_components], ["1.1"])
        self.assertEqual(set(sub_analyses), {"1.1"})
        self.assertEqual(mock_save_analysis.call_count, 1)

    @patch("diagram_analysis.diagram_generator.plan_scope_result_update")
    def test_removed_only_hierarchy_update_marks_scope_for_relation_refresh(self, plan_update):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        clustering = ClusterScopeResult(scope_id=ROOT_SCOPE_ID)
        relation_context = ScopeRelationContext(clustering=clustering)
        incremental_agent = MagicMock()
        incremental_agent.update_scope.return_value = ScopeUpdateResult(
            relation_context=relation_context,
            removed_ids={"2"},
        )
        gen.incremental_agent = incremental_agent
        plan_update.return_value = ScopeUpdateDecision(operations=[])
        scope = AnalysisInsights(description="root", components=[], components_relations=[])

        result = gen._apply_incremental_hierarchy(clustering, scope, {})

        self.assertEqual(result.relation_contexts, {ROOT_SCOPE_ID: relation_context})

    @patch("diagram_analysis.diagram_generator.plan_scope_result_update")
    def test_precomputed_incremental_hierarchy_updates_root_and_nested_scopes(self, plan_update):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=3,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        root_context = ScopeRelationContext(clustering=ClusterScopeResult(scope_id=ROOT_SCOPE_ID))
        child_context = ScopeRelationContext(clustering=ClusterScopeResult(scope_id="1"))
        incremental_agent = MagicMock()
        incremental_agent.update_scope.side_effect = [
            ScopeUpdateResult(relation_context=root_context, refresh_ids={"1"}),
            ScopeUpdateResult(relation_context=child_context, refresh_ids={"1.1"}),
        ]
        gen.incremental_agent = incremental_agent
        root = AnalysisInsights(description="root", components=[], components_relations=[])
        hierarchy = ClusterScopeResult(
            scope_id=ROOT_SCOPE_ID,
            groups=[ClusterGroup(group_id="1", cluster_ids=[1], children=ClusterScopeResult(scope_id="1"))],
        )
        plan_update.side_effect = [ScopeUpdateDecision(operations=[]), ScopeUpdateDecision(operations=[])]

        result = gen._apply_incremental_hierarchy(hierarchy, root, {"1": root.model_copy()})

        self.assertEqual(incremental_agent.update_scope.call_count, 2)
        self.assertEqual([call.args[0] for call in incremental_agent.update_scope.call_args_list], ["root", "1"])
        self.assertEqual(result.relation_contexts, {"root": root_context, "1": child_context})

    def test_incremental_lineage_check_is_scoped_by_language(self):
        baseline = MagicMock()
        baseline.get_languages.return_value = [Language.TYPESCRIPT]
        cache = ClusterCache()
        baseline.get_clusters.return_value = cache
        graph = CallGraph(language="typescript")
        graph.add_node(Node("pkg.live", NodeType.FUNCTION, "/repo/pkg.py", 1, 2))

        partitions = ClusteringService()._incremental_scope_partitions(
            baseline,
            "1",
            {"typescript": graph},
            {"python": {"pkg.live"}},
            self.output_dir,
        )

        self.assertTrue(partitions["typescript"].clusters)
        cache.record_unclustered({"pkg.live"}, "1")
        partitions = ClusteringService()._incremental_scope_partitions(
            baseline,
            "1",
            {"typescript": graph},
            {"typescript": {"pkg.live"}},
            self.output_dir,
        )
        self.assertTrue(partitions["typescript"].clusters)

        cache.record_unclustered(set(), "1")
        with self.assertRaisesRegex(IncrementalCacheMissingError, "persisted scope '1'.*pkg.live"):
            ClusteringService()._incremental_scope_partitions(
                baseline,
                "1",
                {"typescript": graph},
                {"typescript": {"pkg.live"}},
                self.output_dir,
            )

    @patch("static_analyzer.clustering.service.delta_for_language")
    def test_incremental_child_scope_reserves_ids_from_removed_languages(self, delta_for_language):
        baseline = MagicMock()
        baseline.get_languages.return_value = [Language.PYTHON, Language.TYPESCRIPT]
        baseline_paths = {
            Language.PYTHON: MethodClusterPaths({"py.deleted": {"1.9"}}),
            Language.TYPESCRIPT: MethodClusterPaths({"ts.live": {"1.2"}}),
        }
        baseline_caches = {
            language: ClusterCache(method_paths=method_paths) for language, method_paths in baseline_paths.items()
        }
        baseline.get_clusters.side_effect = lambda language: baseline_caches[language]
        graph = CallGraph(language="typescript")
        graph.add_node(Node("ts.live", NodeType.FUNCTION, "/repo/live.ts", 1, 2))
        graph.add_node(Node("ts.replacement", NodeType.FUNCTION, "/repo/new.ts", 6, 7))
        delta_for_language.return_value = LanguageDelta(
            language="typescript",
            cluster_results=ClusterResult(
                clusters={
                    2: {"ts.live"},
                    10: {"ts.replacement"},
                }
            ),
        )

        partitions = ClusteringService()._incremental_scope_partitions(
            baseline,
            "1",
            {"typescript": graph},
            {"typescript": {"ts.live"}},
            self.output_dir,
        )

        self.assertEqual(delta_for_language.call_args.kwargs["next_new_id"], 10)
        self.assertEqual(set(partitions["typescript"].clusters), {2, 10})

    @patch.object(ClusteringService, "build_incremental_hierarchy")
    @patch("diagram_analysis.diagram_generator.save_analysis")
    @patch("diagram_analysis.diagram_generator.prune_empty_components", return_value=set())
    @patch("diagram_analysis.diagram_generator.IncrementalAgent")
    @patch("diagram_analysis.diagram_generator.compute_cluster_delta")
    @patch("diagram_analysis.diagram_generator.snapshot_from_static_analysis")
    def test_incremental_refresh_updates_existing_parent_scope(
        self,
        mock_snapshot,
        mock_delta,
        _mock_incremental_agent,
        _mock_prune,
        mock_save_analysis,
        mock_build_hierarchy,
    ):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        gen.details_agent = Mock()
        gen.incremental_agent = _mock_incremental_agent.return_value
        gen.static_analysis = Mock()
        gen.static_analysis.get_languages.return_value = []
        base_static_analysis = Mock()
        gen.static_analysis.incremental_base_results = base_static_analysis
        gen.static_analysis.available_cfgs.return_value = {}
        gen._generate_subcomponents = Mock()
        gen._persist_static_analysis_artifact = Mock()

        root_component = Component(name="Parent", description="", key_entities=[], component_id="1")
        child_component = Component(
            name="Stable Child",
            description="",
            key_entities=[],
            component_id="1.1",
            file_methods=[
                FileMethodGroup(
                    file_path="pkg/module.py",
                    methods=[MethodEntry(qualified_name="pkg.changed", start_line=1, end_line=10, node_type="METHOD")],
                )
            ],
        )
        root_sibling = Component(name="Other Parent", description="", key_entities=[], component_id="2")
        child_sibling = Component(name="Other Child", description="", key_entities=[], component_id="1.2")
        root_analysis = AnalysisInsights(
            description="root", components=[root_component, root_sibling], components_relations=[]
        )
        sub_analyses = {
            "1": AnalysisInsights(
                description="sub", components=[child_component, child_sibling], components_relations=[]
            )
        }

        mock_snapshot.return_value.all_cluster_ids.return_value = {1}
        mock_delta.return_value.has_changes = True
        mock_delta.return_value.cluster_results.return_value = {}
        mock_build_hierarchy.return_value = ClusterScopeResult(
            scope_id=ROOT_SCOPE_ID,
            groups=[ClusterGroup(group_id="1", cluster_ids=[])],
        )
        _mock_incremental_agent.return_value.update_scope.return_value = ScopeUpdateResult(
            relation_context=ScopeRelationContext(clustering=mock_build_hierarchy.return_value),
            refresh_ids={"1"},
            new_component_ids=set(),
        )
        mock_save_analysis.return_value = self.output_dir / "analysis.json"

        gen.generate_analysis_incremental(root_analysis, sub_analyses)

        self.assertIs(gen.incremental_agent, _mock_incremental_agent.return_value)
        mock_snapshot.assert_called_once_with(base_static_analysis)
        _mock_incremental_agent.return_value.update_scope.assert_called_once()
        scope_id, scope, decision, _clusters = _mock_incremental_agent.return_value.update_scope.call_args.args
        self.assertEqual(scope_id, "root")
        self.assertIs(scope, root_analysis)
        self.assertIsInstance(decision, ScopeUpdateDecision)
        gen._generate_subcomponents.assert_not_called()
        self.assertEqual(sub_analyses["1"].components[0].name, "Stable Child")

    @patch.object(ClusteringService, "build_incremental_hierarchy")
    @patch("diagram_analysis.diagram_generator.save_analysis")
    @patch("diagram_analysis.diagram_generator.prune_empty_components", return_value=set())
    @patch("diagram_analysis.diagram_generator.IncrementalAgent")
    @patch("diagram_analysis.diagram_generator.compute_cluster_delta")
    @patch("diagram_analysis.diagram_generator.snapshot_from_static_analysis")
    def test_incremental_refresh_skips_child_scope_absent_from_hierarchy(
        self,
        mock_snapshot,
        mock_delta,
        _mock_incremental_agent,
        _mock_prune,
        mock_save_analysis,
        mock_build_hierarchy,
    ):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        gen.details_agent = Mock()
        gen.incremental_agent = _mock_incremental_agent.return_value
        gen.static_analysis = Mock()
        gen.static_analysis.get_languages.return_value = []
        gen.static_analysis.incremental_base_results = Mock()
        gen.static_analysis.available_cfgs.return_value = {}
        gen._generate_subcomponents = Mock()
        gen._persist_static_analysis_artifact = Mock()

        root_component = Component(name="Parent", description="", key_entities=[], component_id="1")
        root_analysis = AnalysisInsights(description="root", components=[root_component], components_relations=[])
        sub_analyses = {"1": AnalysisInsights(description="sub", components=[], components_relations=[])}

        mock_snapshot.return_value.all_cluster_ids.return_value = {1}
        mock_delta.return_value.has_changes = True
        mock_delta.return_value.cluster_results.return_value = {}
        mock_build_hierarchy.return_value = ClusterScopeResult(
            scope_id=ROOT_SCOPE_ID,
            groups=[ClusterGroup(group_id="1", cluster_ids=[])],
        )
        _mock_incremental_agent.return_value.update_scope.return_value = ScopeUpdateResult(
            relation_context=ScopeRelationContext(clustering=mock_build_hierarchy.return_value),
            refresh_ids={"1"},
            new_component_ids=set(),
        )
        mock_save_analysis.return_value = self.output_dir / "analysis.json"

        gen.generate_analysis_incremental(root_analysis, sub_analyses)

        self.assertEqual(_mock_incremental_agent.return_value.update_scope.call_count, 1)
        gen._generate_subcomponents.assert_not_called()

    @patch("diagram_analysis.diagram_generator.prune_empty_components", return_value=set())
    @patch("diagram_analysis.diagram_generator.compute_cluster_delta")
    @patch("diagram_analysis.diagram_generator.snapshot_from_static_analysis")
    def test_incremental_new_component_uses_precomputed_scope_before_subcomponent_generation(
        self,
        mock_snapshot,
        mock_delta,
        _mock_prune,
    ):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        gen.details_agent = Mock()
        gen.incremental_agent = Mock()
        gen.static_analysis = Mock()
        gen.static_analysis.get_languages.return_value = []
        gen.static_analysis.incremental_base_results = Mock()
        gen.static_analysis.available_cfgs.return_value = {}

        existing = Component(name="Existing", description="", key_entities=[], component_id="1")
        created = Component(name="Created", description="", key_entities=[], component_id="2")
        root_analysis = AnalysisInsights(description="root", components=[existing], components_relations=[])
        sub_analyses: dict[str, AnalysisInsights] = {}

        mock_snapshot.return_value.all_cluster_ids.return_value = {1}
        mock_delta.return_value.has_changes = True
        mock_delta.return_value.cluster_results.return_value = {}

        def apply_incremental(*_args):
            root_analysis.components.append(created)
            return RecursiveScopeUpdateResult(refresh_ids={"2"}, new_component_ids={"2"})

        call_order: list[str] = []

        def generate_subcomponents(*_args):
            call_order.append("generate")
            return [], sub_analyses

        hierarchy = ClusterScopeResult(
            scope_id=ROOT_SCOPE_ID,
            groups=[
                ClusterGroup(
                    group_id="2",
                    cluster_ids=[],
                    children=ClusterScopeResult(scope_id="2"),
                )
            ],
        )
        gen._apply_incremental_hierarchy = Mock(side_effect=apply_incremental)
        gen._generate_subcomponents = Mock(side_effect=generate_subcomponents)
        gen._refresh_files_index = Mock()
        gen.finalize_and_save = Mock(return_value=self.output_dir / "analysis.json")

        with patch.object(ClusteringService, "build_incremental_hierarchy", return_value=hierarchy):
            gen.generate_analysis_incremental(root_analysis, sub_analyses)

        self.assertEqual(call_order, ["generate"])
        gen._apply_incremental_hierarchy.assert_called_once_with(hierarchy, root_analysis, sub_analyses)
        gen._generate_subcomponents.assert_called_once_with(root_analysis, [created], sub_analyses)
        gen.incremental_agent.detail_new_components.assert_called_once_with([created])

    @patch("diagram_analysis.diagram_generator.save_analysis")
    @patch("diagram_analysis.diagram_generator.prune_empty_components")
    @patch("diagram_analysis.diagram_generator.compute_cluster_delta")
    @patch("diagram_analysis.diagram_generator.snapshot_from_static_analysis")
    def test_empty_incremental_delta_does_not_prune_stable_leaf_components(
        self,
        mock_snapshot,
        mock_delta,
        mock_prune,
        mock_save_analysis,
    ):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=3,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        gen.details_agent = Mock()
        gen.incremental_agent = Mock()
        gen.static_analysis = Mock()
        gen.static_analysis.get_languages.return_value = []
        gen.static_analysis.incremental_base_results = Mock()
        gen.static_analysis.available_cfgs.return_value = {}
        gen._persist_static_analysis_artifact = Mock()

        root = Component(name="Root", description="", key_entities=[], component_id="1")
        parent = Component(name="Parent", description="", key_entities=[], component_id="1.1")
        empty_leaf = Component(name="Stable Leaf", description="", key_entities=[], component_id="1.1.1")
        root_analysis = AnalysisInsights(
            description="root",
            components=[root, Component(name="Other Root", description="", key_entities=[], component_id="2")],
            components_relations=[],
        )
        sub_analyses = {
            "1": AnalysisInsights(
                description="sub",
                components=[
                    parent,
                    Component(name="Other Parent", description="", key_entities=[], component_id="1.2"),
                ],
                components_relations=[],
            ),
            "1.1": AnalysisInsights(
                description="leaf",
                components=[
                    empty_leaf,
                    Component(name="Other Leaf", description="", key_entities=[], component_id="1.1.2"),
                ],
                components_relations=[],
            ),
        }

        mock_snapshot.return_value.all_cluster_ids.return_value = {1}
        mock_delta.return_value.has_changes = False
        mock_save_analysis.return_value = self.output_dir / "analysis.json"

        with patch("diagram_analysis.diagram_generator.build_files_index", return_value={}) as mock_build_index:
            gen.generate_analysis_incremental(root_analysis, sub_analyses)

        mock_prune.assert_not_called()
        self.assertEqual(sub_analyses["1.1"].components[0].name, "Stable Leaf")
        self.assertIsNone(gen.abstraction_agent)
        self.assertEqual(mock_build_index.call_count, 1 + len(sub_analyses))

    def test_group_membership_change_applies_hierarchy_when_cluster_delta_is_empty(self):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        delta = MagicMock()
        delta.has_changes = False
        delta.cluster_results.return_value = {}
        hierarchy = ClusterScopeResult(scope_id=ROOT_SCOPE_ID)
        gen.static_analysis = Mock()
        gen.details_agent = Mock()
        gen.incremental_agent = Mock()
        gen.clustering_hierarchy = hierarchy
        gen._incremental_preparation = _IncrementalPreparation(
            delta=delta,
            baseline_membership=_MembershipBaseline(),
            has_membership_changes=True,
        )
        gen._apply_incremental_hierarchy = Mock(return_value=RecursiveScopeUpdateResult())
        gen._refresh_files_index = Mock()
        gen.finalize_and_save = Mock(return_value=self.output_dir / "analysis.json")
        root_analysis = AnalysisInsights(description="root", components=[], components_relations=[])

        result = gen.generate_analysis_incremental(root_analysis, {})

        self.assertEqual(result, self.output_dir / "analysis.json")
        gen._apply_incremental_hierarchy.assert_called_once_with(hierarchy, root_analysis, {})
        gen.finalize_and_save.assert_called_once_with(root_analysis, {}, seed_delta={})

    def test_refresh_files_index_reuses_sources_and_copies_sub_entries(self):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        gen.static_analysis = MagicMock(spec=StaticAnalysisResults)
        root_analysis = AnalysisInsights(description="root", components=[], components_relations=[])
        sub_analysis = AnalysisInsights(description="sub", components=[], components_relations=[])
        root_method = MethodEntry(qualified_name="root.method", start_line=1, end_line=2, node_type="FUNCTION")
        shared_sub_method = MethodEntry(qualified_name="sub.method", start_line=3, end_line=4, node_type="FUNCTION")
        sub_only_method = MethodEntry(qualified_name="sub.only", start_line=5, end_line=6, node_type="FUNCTION")
        root_entry = FileEntry(methods=[root_method])
        shared_sub_entry = FileEntry(methods=[shared_sub_method])
        sub_only_entry = FileEntry(methods=[sub_only_method])

        with (
            patch("diagram_analysis.diagram_generator.refresh_method_spans_from_cfg"),
            patch("diagram_analysis.diagram_generator.index_relation_endpoints"),
            patch(
                "diagram_analysis.diagram_generator.build_files_index",
                side_effect=[
                    {"shared.py": root_entry},
                    {"shared.py": shared_sub_entry, "sub.py": sub_only_entry},
                ],
            ) as mock_build_index,
        ):
            gen._refresh_files_index(root_analysis, {"1": sub_analysis})

        root_methods = {method.qualified_name: method for method in root_analysis.files["shared.py"].methods}
        self.assertIsNot(root_methods["sub.method"], shared_sub_method)
        self.assertIsNot(root_analysis.files["sub.py"], sub_only_entry)
        self.assertIsNot(root_analysis.files["sub.py"].methods[0], sub_only_method)
        self.assertIs(mock_build_index.call_args_list[0].args[2], mock_build_index.call_args_list[1].args[2])

    def test_persist_static_analysis_artifact_saves_cluster_cache_with_injected_analyzer(self):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=1,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        gen._static_analyzer = Mock()
        gen.source_sha = "sha-current"

        cfg = CallGraph(language="python")
        cfg.add_node(
            Node(
                fully_qualified_name="test.fn",
                node_type=NodeType.FUNCTION,
                file_path=str(self.repo_location / "test.py"),
                line_start=1,
                line_end=1,
            )
        )
        results = StaticAnalysisResults()
        results.add_cfg(Language.PYTHON, cfg)
        results.get_clusters(Language.PYTHON).adopt(
            ClusterResult(
                clusters={1: {"test.fn"}},
                cluster_to_files={1: {str(self.repo_location / "test.py")}},
                file_to_clusters={str(self.repo_location / "test.py"): {1}},
                strategy="test",
            )
        )
        gen.static_analysis = results

        gen._persist_static_analysis_artifact()

        loaded = StaticAnalysisCache(self.output_dir, self.repo_location).load_with_sha()
        self.assertIsNotNone(loaded)
        if loaded is None:
            return
        loaded_results, cached_sha = loaded
        self.assertEqual(cached_sha, "sha-current")
        self.assertIsNotNone(loaded_results.get_clusters(Language.PYTHON).result)

    def _finalize_gen(self):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=1,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        gen.finalize_for_save = Mock()
        gen._write_file_coverage = Mock()
        gen._persist_static_analysis_artifact = Mock()
        return gen

    @patch("diagram_analysis.diagram_generator.save_analysis")
    def test_finalize_and_save_persists_side_artifacts_by_default(self, mock_save):
        mock_save.return_value = self.output_dir / "analysis.json"
        gen = self._finalize_gen()
        analysis = AnalysisInsights(description="d", components=[], components_relations=[])

        gen.finalize_and_save(analysis, {})

        gen._write_file_coverage.assert_called_once()
        gen._persist_static_analysis_artifact.assert_called_once()

    @patch("diagram_analysis.diagram_generator.save_analysis")
    def test_finalize_and_save_skips_side_artifacts_for_partial(self, mock_save):
        """Component expansion (partial) must not rewrite file_coverage.json or touch
        the static-analysis cache/SHA tag — that would regress the next incremental run."""
        mock_save.return_value = self.output_dir / "analysis.json"
        gen = self._finalize_gen()
        analysis = AnalysisInsights(description="d", components=[], components_relations=[])

        gen.finalize_and_save(analysis, {}, persist_side_artifacts=False)

        # The analysis is still finalized and saved.
        gen.finalize_for_save.assert_called_once_with(analysis, {})
        mock_save.assert_called_once()
        gen._write_file_coverage.assert_not_called()
        gen._persist_static_analysis_artifact.assert_not_called()

    @patch("diagram_analysis.diagram_generator.save_analysis", side_effect=OSError("write failed"))
    def test_failed_authoritative_save_discards_pending_cache_without_flushing_live_cache(self, mock_save):
        gen = self._finalize_gen()
        results = StaticAnalysisResults()
        results.get_clusters(Language.PYTHON).record_unclustered({"baseline"})
        live_cache = results.get_clusters(Language.PYTHON)
        gen.static_analysis = results
        gen._pending_cluster_caches = {"python": live_cache.detached_copy()}
        gen._pending_cluster_caches["python"].record_unclustered({"prepared"})
        analysis = AnalysisInsights(description="d", components=[], components_relations=[])

        with self.assertRaisesRegex(OSError, "write failed"):
            gen.finalize_and_save(analysis, {})

        self.assertEqual(live_cache.get_unclustered_members(), {"baseline"})
        self.assertIsNone(gen._pending_cluster_caches)
        gen._persist_static_analysis_artifact.assert_not_called()

    @patch("diagram_analysis.diagram_generator.save_analysis")
    def test_successful_authoritative_save_installs_pending_lineage_before_persistence(self, mock_save):
        mock_save.return_value = self.output_dir / "analysis.json"
        gen = self._finalize_gen()
        results = StaticAnalysisResults()
        live_cache = results.get_clusters(Language.PYTHON)
        gen.static_analysis = results
        pending = live_cache.detached_copy()
        pending.record_unclustered({"committed"}, "1")
        gen._pending_cluster_caches = {"python": pending}
        gen._persist_static_analysis_artifact.side_effect = lambda: self.assertIs(
            results.get_clusters(Language.PYTHON), pending
        )
        analysis = AnalysisInsights(description="d", components=[], components_relations=[])

        gen.finalize_and_save(analysis, {})

        self.assertIs(results.get_clusters(Language.PYTHON), pending)
        self.assertEqual(results.get_clusters(Language.PYTHON).get_unclustered_members("1"), {"committed"})
        gen._persist_static_analysis_artifact.assert_called_once()

    @patch("diagram_analysis.diagram_generator.assert_scope_containment")
    @patch("diagram_analysis.diagram_generator.absorb_single_child_components", return_value=[])
    def test_absorption_receives_pending_cache_not_live_cache(self, mock_absorb, _mock_containment):
        gen = self._finalize_gen()
        gen.finalize_for_save = DiagramGenerator.finalize_for_save.__get__(gen)
        gen._strip_ignored = Mock()
        gen.rebuild_global_relations = Mock()
        results = StaticAnalysisResults()
        live_cache = results.get_clusters(Language.PYTHON)
        pending = live_cache.detached_copy()
        gen.static_analysis = results
        gen._pending_cluster_caches = {"python": pending}
        analysis = AnalysisInsights(description="d", components=[], components_relations=[])

        gen.finalize_for_save(analysis, {})

        self.assertEqual(mock_absorb.call_args.args[2], [pending])
        self.assertIsNot(mock_absorb.call_args.args[2][0], live_cache)


if __name__ == "__main__":
    unittest.main()
