import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    Relation,
    RelationCallSite,
    RelationEdge,
    ScopeOperation,
    ScopeOperationAction,
    ScopedClusterRef,
    ScopeUpdateDecision,
    SourceCodeReference,
    assign_component_ids,
)
from agents.file_index_models import FileEntry, FileMethodGroup, MethodEntry
from agents.incremental_results import ScopeRelationContext, ScopeUpdateResult
from agents.relation_edges import index_relation_endpoints
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
from agents.incremental_agent import _preserve_unchanged_relations
from diagram_analysis.cluster_delta import ClusterMemberDelta, ClusterRef, LanguageStructuralDiff, StructuralClusterDiff
from diagram_analysis.diagram_generator import (
    DiagramGenerator,
    _capture_membership_baseline,
    _component_depth,
    _component_expansion_seeds,
    _incremental_changed_component_ids,
    _preserve_unchanged_global_relations,
    _restore_unchanged_membership,
    _restore_unchanged_metadata,
    _restore_unchanged_subtrees,
)
from diagram_analysis.exceptions import IncrementalCacheMissingError
from static_analyzer.analysis_cache import StaticAnalysisCache
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering import ClusterResult
from static_analyzer.constants import Language, NodeType
from static_analyzer.infomap_clustering import HierarchicalInfomapClusterer
from static_analyzer.program_graph import ProgramGraph
from tests.program_graph_factory import make_symbol


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
            metadata=AnalysisMetadata(generated_at="2026-01-01T00:00:00Z", repo_name="test", depth_level=1),
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
            metadata=AnalysisMetadata(generated_at="2026-01-01T00:00:00Z", repo_name="test", depth_level=1),
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
            build_unified_analysis_json(self.analysis, [], "repo", repo_dir=self.repo_dir, source_tree_hash="")
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
            build_unified_analysis_json(self.analysis, [], "repo", repo_dir=self.repo_dir, source_tree_hash="")
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
            build_unified_analysis_json(self.analysis, [], "repo", repo_dir=self.repo_dir, source_tree_hash="")
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
            build_unified_analysis_json(self.analysis, [], "repo", repo_dir=self.repo_dir, source_tree_hash="")
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
            build_unified_analysis_json(self.analysis, [], "repo", repo_dir=self.repo_dir, source_tree_hash=precomputed)
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
        self.assertIsNone(gen.incremental_planning_agent)
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
    def test_pre_analysis(
        self,
        mock_abstraction,
        mock_details,
        mock_meta,
        mock_initialize_llms,
        mock_get_static_analysis,
        mock_scanner,
    ):
        # Test pre_analysis method
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

        with (
            patch("diagram_analysis.diagram_generator.IncrementalPlanningAgent") as mock_incremental_planning,
            patch("diagram_analysis.diagram_generator.IncrementalAgent") as mock_incremental,
        ):
            gen.pre_analysis()

        # Verify agents were created
        self.assertIsNotNone(gen.meta_agent)
        self.assertIsNotNone(gen.details_agent)
        self.assertIsNotNone(gen.abstraction_agent)
        self.assertIs(gen.incremental_planning_agent, mock_incremental_planning.return_value)
        self.assertIs(gen.incremental_agent, mock_incremental.return_value)
        mock_meta_instance.analyze_project_metadata.assert_called_once_with(skip_cache=False)

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
        component = Component(name="TestComponent", description="Test", key_entities=[])

        result_name, result_analysis, new_components = gen.process_component(component)

        # Should return None and empty list on exception
        self.assertIsNone(result_name)
        self.assertIsNone(result_analysis)
        self.assertEqual(new_components, [])

    @patch("diagram_analysis.diagram_generator.save_analysis")
    @patch("diagram_analysis.diagram_generator.get_expandable_components")
    def test_generate_analysis_frontier_submits_child_before_slow_sibling_finishes(
        self, mock_get_expandable_components, mock_save_analysis
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

        gen.abstraction_agent = Mock()
        gen.abstraction_agent.run.return_value = (root_analysis, {})
        gen.details_agent = Mock()  # pre_analysis is skipped when details/abstraction are already initialized
        mock_get_expandable_components.return_value = [root_a, root_b]
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

        result = gen.generate_analysis()

        self.assertEqual(result, (self.output_dir / "analysis.json").resolve())
        self.assertIn("child_start", timestamps)
        self.assertIn("b_end", timestamps)
        self.assertLess(timestamps["child_start"], timestamps["b_end"])

        processed_names = [call.args[0].name for call in gen._process_component.call_args_list]
        self.assertIn("A-child", processed_names)

    def test_generate_analysis_uses_root_expandables_for_can_expand(self):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=1,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )

        # Prevent pre_analysis from running.
        gen.abstraction_agent = Mock()
        gen.details_agent = Mock()

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

        gen.abstraction_agent.run.return_value = (analysis, {})

        planned = [analysis.components[0]]
        captured: dict[str, list[Component]] = {}

        def _capture_build(
            *,
            analysis,
            expandable_components,
            repo_name,
            repo_dir,
            source_tree_hash,
            sub_analyses,
            file_coverage_summary,
        ):
            captured["expandable_components"] = expandable_components
            return "{}"

        with patch("diagram_analysis.diagram_generator.get_expandable_components", return_value=planned):
            with patch(
                "diagram_analysis.io_utils.build_unified_analysis_json",
                side_effect=_capture_build,
            ):
                gen.generate_analysis()

        # Both components have files, so both should be expandable (io_utils merges caller-provided with computed)
        self.assertEqual(
            sorted([c.component_id for c in captured["expandable_components"]]),
            sorted([c.component_id for c in analysis.components]),
        )

    @patch("diagram_analysis.diagram_generator.get_expandable_components")
    def test_generate_analysis_depth_one_preserves_root_expandable_flags(self, mock_get_expandable_components):
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

        mock_get_expandable_components.return_value = analysis.components

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
        gen.incremental_planning_agent = Mock()
        gen.incremental_agent = Mock()
        gen.abstraction_agent.run.return_value = (analysis, {})

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
        gen.incremental_planning_agent = Mock()
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
    @patch("diagram_analysis.diagram_generator.get_expandable_components")
    def test_generate_subcomponents_respects_absolute_depth(
        self,
        mock_get_expandable_components,
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

        gen.details_agent.run.return_value = (child_analysis, {})
        mock_get_expandable_components.return_value = [generated_child]

        expanded_components, sub_analyses = gen._generate_subcomponents(root_analysis, [depth_two, max_depth_leaf])

        gen.details_agent.run.assert_called_once_with(depth_two)
        self.assertEqual([component.component_id for component in expanded_components], ["1.1"])
        self.assertEqual(set(sub_analyses), {"1.1"})
        self.assertEqual(mock_save_analysis.call_count, 1)

    def test_removed_only_incremental_update_marks_scope_for_relation_refresh(self):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=2,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        planning_agent = MagicMock()
        incremental_agent = MagicMock()
        relation_context = ScopeRelationContext(cluster_results={}, cfg_graphs={})
        incremental_agent.update_scope.return_value = ScopeUpdateResult(
            relation_context=relation_context,
            removed_ids={"2"},
        )
        gen.incremental_planning_agent = planning_agent
        gen.incremental_agent = incremental_agent
        scope = AnalysisInsights(description="root", components=[], components_relations=[])

        result = gen._apply_incremental_scope_recursively(
            "root",
            scope,
            StructuralClusterDiff(),
            {},
            {},
        )

        self.assertEqual(result.relation_contexts, {"root": relation_context})

    @patch("diagram_analysis.diagram_generator._build_scope_incremental_inputs")
    def test_recursive_scope_update_aggregates_relation_contexts(self, mock_build_scope_inputs):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=3,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        root_context = ScopeRelationContext(cluster_results={}, cfg_graphs={})
        child_context = ScopeRelationContext(
            cluster_results={"python": ClusterResult(clusters={2: {"pkg.changed"}})},
            cfg_graphs={},
        )
        incremental_agent = MagicMock()
        incremental_agent.update_scope.side_effect = [
            ScopeUpdateResult(relation_context=root_context, refresh_ids={"1"}),
            ScopeUpdateResult(relation_context=child_context, refresh_ids={"1.1"}),
        ]
        gen.incremental_planning_agent = MagicMock()
        gen.incremental_agent = incremental_agent
        root_component = Component(name="Parent", description="", key_entities=[], component_id="1")
        child_component = Component(
            name="Child",
            description="",
            key_entities=[],
            component_id="1.1",
            file_methods=[
                FileMethodGroup(
                    file_path="pkg/module.py",
                    methods=[
                        MethodEntry(
                            qualified_name="pkg.changed",
                            start_line=1,
                            end_line=2,
                            node_type="FUNCTION",
                        )
                    ],
                )
            ],
        )
        root = AnalysisInsights(description="root", components=[root_component], components_relations=[])
        child = AnalysisInsights(description="child", components=[child_component], components_relations=[])
        child_diff = StructuralClusterDiff(
            by_language={
                "python": LanguageStructuralDiff(
                    language="python",
                    modified=[
                        ClusterMemberDelta(
                            old_cluster=ClusterRef(language="python", cluster_id=2),
                            new_cluster=ClusterRef(language="python", cluster_id=2),
                            removed_methods={"pkg.changed"},
                        )
                    ],
                )
            }
        )
        mock_build_scope_inputs.return_value = (child_context.cluster_results, child_diff)

        result = gen._apply_incremental_scope_recursively(
            "root",
            root,
            StructuralClusterDiff(),
            {},
            {"1": child},
        )

        self.assertEqual(result.relation_contexts, {"root": root_context, "1": child_context})

    @patch("diagram_analysis.diagram_generator.save_analysis")
    @patch("diagram_analysis.diagram_generator.prune_empty_components", return_value=set())
    @patch("diagram_analysis.diagram_generator._build_scope_incremental_inputs")
    @patch("diagram_analysis.diagram_generator.structural_diff_from_delta")
    @patch("diagram_analysis.diagram_generator.IncrementalPlanningAgent")
    @patch("diagram_analysis.diagram_generator.IncrementalAgent")
    @patch("diagram_analysis.diagram_generator.compute_cluster_delta")
    @patch("diagram_analysis.diagram_generator.snapshot_from_static_analysis")
    def test_incremental_refresh_updates_existing_parent_scope(
        self,
        mock_snapshot,
        mock_delta,
        _mock_incremental_agent,
        mock_planning_agent,
        _mock_structural_diff,
        mock_build_scope_inputs,
        _mock_prune,
        mock_save_analysis,
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
        gen.incremental_planning_agent = mock_planning_agent.return_value
        gen.incremental_agent = _mock_incremental_agent.return_value
        gen.static_analysis = Mock()
        gen.static_analysis.get_languages.return_value = []
        base_static_analysis = Mock()
        gen.static_analysis.incremental_base_results = base_static_analysis
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
        root_analysis = AnalysisInsights(description="root", components=[root_component], components_relations=[])
        sub_analyses = {"1": AnalysisInsights(description="sub", components=[child_component], components_relations=[])}

        mock_snapshot.return_value.all_cluster_ids.return_value = {1}
        mock_delta.return_value.has_changes = True
        mock_delta.return_value.cluster_results.return_value = {}
        root_diff = StructuralClusterDiff(
            by_language={
                "python": LanguageStructuralDiff(
                    language="python",
                    modified=[
                        ClusterMemberDelta(
                            old_cluster=ClusterRef(language="python", cluster_id=2),
                            new_cluster=ClusterRef(language="python", cluster_id=2),
                            added_methods={"pkg.changed"},
                        )
                    ],
                )
            }
        )
        child_diff = StructuralClusterDiff(
            by_language={
                "python": LanguageStructuralDiff(
                    language="python",
                    modified=[
                        ClusterMemberDelta(
                            old_cluster=ClusterRef(language="python", cluster_id=3, scope_id="1"),
                            new_cluster=ClusterRef(language="python", cluster_id=3, scope_id="1"),
                            removed_methods={"pkg.changed"},
                        )
                    ],
                )
            }
        )
        _mock_structural_diff.return_value = root_diff
        mock_build_scope_inputs.return_value = ({"python": ClusterResult(clusters={3: {"pkg.changed"}})}, child_diff)
        root_decision = ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.UPDATE_COMPONENT,
                    cluster_refs=[ScopedClusterRef(scope_id="", language="python", cluster_id=2)],
                    component_id="1",
                    rationale="Parent owns changed root cluster.",
                )
            ]
        )
        child_decision = ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.UPDATE_COMPONENT,
                    cluster_refs=[ScopedClusterRef(scope_id="1", language="python", cluster_id=3)],
                    component_id="1.1",
                    rationale="Child owns changed local cluster.",
                )
            ]
        )
        mock_planning_agent.return_value.decide_scope_update.side_effect = [root_decision, child_decision]
        _mock_incremental_agent.return_value.update_scope.side_effect = [
            ScopeUpdateResult(
                relation_context=ScopeRelationContext(cluster_results={}, cfg_graphs={}),
                refresh_ids={"1"},
                new_component_ids=set(),
            ),
            ScopeUpdateResult(
                relation_context=ScopeRelationContext(cluster_results={}, cfg_graphs={}),
                refresh_ids={"1.1"},
                new_component_ids=set(),
            ),
        ]
        _mock_incremental_agent.return_value._create_strict_component_subgraph.return_value = ("", {}, {})
        mock_save_analysis.return_value = self.output_dir / "analysis.json"

        gen.generate_analysis_incremental(root_analysis, sub_analyses)

        self.assertIs(gen.incremental_planning_agent, mock_planning_agent.return_value)
        self.assertIs(gen.incremental_agent, _mock_incremental_agent.return_value)
        mock_snapshot.assert_called_once_with(base_static_analysis)
        mock_planning_agent.return_value.decide_scope_update.assert_called_once_with(
            "root",
            root_analysis,
            root_diff,
            {},
        )
        _mock_incremental_agent.return_value.update_scope.assert_called_once()
        mock_build_scope_inputs.assert_called_once_with(
            root_component,
            "1",
            _mock_incremental_agent.return_value,
            gen.changes,
            gen.repo_location,
            gen._changed_members,
        )
        gen._generate_subcomponents.assert_not_called()
        self.assertEqual(sub_analyses["1"].components[0].name, "Stable Child")

    @patch("diagram_analysis.diagram_generator.save_analysis")
    @patch("diagram_analysis.diagram_generator.prune_empty_components", return_value=set())
    @patch("diagram_analysis.diagram_generator._build_scope_incremental_inputs")
    @patch("diagram_analysis.diagram_generator.structural_diff_from_delta")
    @patch("diagram_analysis.diagram_generator.IncrementalPlanningAgent")
    @patch("diagram_analysis.diagram_generator.IncrementalAgent")
    @patch("diagram_analysis.diagram_generator.compute_cluster_delta")
    @patch("diagram_analysis.diagram_generator.snapshot_from_static_analysis")
    def test_incremental_refresh_skips_child_scope_when_local_diff_is_empty(
        self,
        mock_snapshot,
        mock_delta,
        _mock_incremental_agent,
        mock_planning_agent,
        _mock_structural_diff,
        mock_build_scope_inputs,
        _mock_prune,
        mock_save_analysis,
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
        gen.incremental_planning_agent = mock_planning_agent.return_value
        gen.incremental_agent = _mock_incremental_agent.return_value
        gen.static_analysis = Mock()
        gen.static_analysis.get_languages.return_value = []
        gen.static_analysis.incremental_base_results = Mock()
        gen._generate_subcomponents = Mock()
        gen._persist_static_analysis_artifact = Mock()

        root_component = Component(name="Parent", description="", key_entities=[], component_id="1")
        root_analysis = AnalysisInsights(description="root", components=[root_component], components_relations=[])
        sub_analyses = {"1": AnalysisInsights(description="sub", components=[], components_relations=[])}

        mock_snapshot.return_value.all_cluster_ids.return_value = {1}
        mock_delta.return_value.has_changes = True
        mock_delta.return_value.cluster_results.return_value = {}
        _mock_structural_diff.return_value = StructuralClusterDiff(
            by_language={"python": LanguageStructuralDiff(language="python", new=[ClusterRef("python", 2)])}
        )
        mock_planning_agent.return_value.decide_scope_update.return_value = ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.UPDATE_COMPONENT,
                    cluster_refs=[ScopedClusterRef(scope_id="", language="python", cluster_id=2)],
                    component_id="1",
                    rationale="Parent changed.",
                )
            ]
        )
        _mock_incremental_agent.return_value.update_scope.return_value = ScopeUpdateResult(
            relation_context=ScopeRelationContext(cluster_results={}, cfg_graphs={}),
            refresh_ids={"1"},
            new_component_ids=set(),
        )
        mock_build_scope_inputs.return_value = ({}, StructuralClusterDiff())
        mock_save_analysis.return_value = self.output_dir / "analysis.json"

        gen.generate_analysis_incremental(root_analysis, sub_analyses)

        self.assertEqual(mock_planning_agent.return_value.decide_scope_update.call_count, 1)
        self.assertEqual(_mock_incremental_agent.return_value.update_scope.call_count, 1)
        gen._generate_subcomponents.assert_not_called()

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
        gen.incremental_planning_agent = Mock()
        gen.incremental_agent = Mock()
        gen.incremental_agent._create_strict_component_subgraph.return_value = ("", {}, {})
        gen.static_analysis = Mock()
        gen.static_analysis.get_languages.return_value = []
        gen.static_analysis.incremental_base_results = Mock()
        gen._persist_static_analysis_artifact = Mock()

        root = Component(name="Root", description="", key_entities=[], component_id="1")
        parent = Component(name="Parent", description="", key_entities=[], component_id="1.1")
        empty_leaf = Component(name="Stable Leaf", description="", key_entities=[], component_id="1.1.1")
        root_analysis = AnalysisInsights(description="root", components=[root], components_relations=[])
        sub_analyses = {
            "1": AnalysisInsights(description="sub", components=[parent], components_relations=[]),
            "1.1": AnalysisInsights(description="leaf", components=[empty_leaf], components_relations=[]),
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

    def test_persist_static_analysis_artifact_saves_infomap_snapshot_without_injected_analyzer(self):
        gen = DiagramGenerator(
            repo_location=self.repo_location,
            temp_folder=self.temp_folder,
            repo_name="test_repo",
            output_dir=self.output_dir,
            depth_level=1,
            run_id="test-run-id",
            log_path="test_repo/test-run-log",
        )
        gen.source_sha = "sha-current"

        graph = ProgramGraph(language="python")
        graph.add_node(
            make_symbol("test.fn", NodeType.FUNCTION, str(self.repo_location / "test.py"), 1, 1, language="python")
        )
        HierarchicalInfomapClusterer().cluster(graph)
        results = StaticAnalysisResults()
        results.add_program_graph(Language.PYTHON, graph)
        gen.static_analysis = results

        gen._persist_static_analysis_artifact()

        loaded = StaticAnalysisCache(self.output_dir, self.repo_location).load_with_sha()
        self.assertIsNotNone(loaded)
        if loaded is None:
            return
        loaded_results, cached_sha = loaded
        self.assertEqual(cached_sha, "sha-current")
        self.assertIsNotNone(loaded_results.get_program_graph(Language.PYTHON).cluster_snapshot)

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
        mock_save.return_value = self.output_dir / "analysis.json"
        gen = self._finalize_gen()
        analysis = AnalysisInsights(description="d", components=[], components_relations=[])

        gen.finalize_and_save(analysis, {}, persist_side_artifacts=False)

        # The analysis itself is still finalized + saved...
        gen.finalize_for_save.assert_called_once_with(analysis, {})
        mock_save.assert_called_once()
        # ...but the external side artifacts are left untouched.
        gen._write_file_coverage.assert_not_called()
        gen._persist_static_analysis_artifact.assert_not_called()


def _pm_method(qname: str, start: int = 1, end: int = 2) -> MethodEntry:
    return MethodEntry(qualified_name=qname, start_line=start, end_line=end, node_type="FUNCTION", content_hash="h")


def _pm_component(name: str, component_id: str, files: dict[str, list[MethodEntry]]) -> Component:
    return Component(
        name=name,
        description=f"{name} description",
        key_entities=[],
        source_group_names=[name.lower()],
        component_id=component_id,
        file_methods=[FileMethodGroup(file_path=fp, methods=methods) for fp, methods in files.items()],
    )


def _pm_owned(component: Component) -> set[tuple[str, str]]:
    return {(group.file_path, method.qualified_name) for group in component.file_methods for method in group.methods}


class TestUnchangedComponentPreservation(unittest.TestCase):
    """The incremental keeps a component with no changed member byte-for-byte identical."""

    def test_unchanged_membership_pinned_back_after_reshuffle(self):
        a = _pm_component("A", "1", {"a.py": [_pm_method("a.a1"), _pm_method("a.a2")]})
        b = _pm_component("B", "2", {"b.py": [_pm_method("b.b1")]})
        c = _pm_component("C", "3", {"c.py": [_pm_method("c.c1")]})
        root = AnalysisInsights(description="root", components=[a, b, c], components_relations=[])
        baseline = _capture_membership_baseline(root, {})

        # The re-partition spuriously grabs B's unchanged method into A; C's body changed.
        a.file_methods = [
            FileMethodGroup(file_path="a.py", methods=[_pm_method("a.a1"), _pm_method("a.a2")]),
            FileMethodGroup(file_path="b.py", methods=[_pm_method("b.b1")]),
        ]
        b.file_methods = []

        _restore_unchanged_membership(root, {}, baseline, {"c.c1"}, set())

        self.assertEqual(_pm_owned(a), {("a.py", "a.a1"), ("a.py", "a.a2")})
        self.assertEqual(_pm_owned(b), {("b.py", "b.b1")})
        self.assertEqual(_pm_owned(c), {("c.py", "c.c1")})

    def test_changed_method_follows_the_repartition(self):
        # A body-changed method the re-partition moved to a new owner is NOT pinned back.
        a = _pm_component("A", "1", {"a.py": [_pm_method("a.a1")]})
        c = _pm_component("C", "3", {"c.py": [_pm_method("c.c1")]})
        root = AnalysisInsights(description="root", components=[a, c], components_relations=[])
        baseline = _capture_membership_baseline(root, {})

        a.file_methods = [
            FileMethodGroup(file_path="a.py", methods=[_pm_method("a.a1")]),
            FileMethodGroup(file_path="c.py", methods=[_pm_method("c.c1")]),
        ]
        c.file_methods = []

        _restore_unchanged_membership(root, {}, baseline, {"c.c1"}, set())

        self.assertEqual(_pm_owned(a), {("a.py", "a.a1"), ("c.py", "c.c1")})
        self.assertEqual(_pm_owned(c), set())

    def test_metadata_restored_and_reported_for_unchanged_only(self):
        a = _pm_component("A", "1", {"a.py": [_pm_method("a.a1")]})
        c = _pm_component("C", "3", {"c.py": [_pm_method("c.c1")]})
        root = AnalysisInsights(description="root", components=[a, c], components_relations=[])
        baseline = _capture_membership_baseline(root, {})

        # The planner reworded both, but only C actually changed.
        a.name, a.description = "A renamed", "reworded"
        c.name, c.description = "C renamed", "genuinely new"

        unchanged = _restore_unchanged_metadata(root, {}, baseline, {"c.c1"})

        self.assertEqual(unchanged, {"1"})
        self.assertEqual((a.name, a.description), ("A", "A description"))
        self.assertEqual((c.name, c.description), ("C renamed", "genuinely new"))

    def test_body_changed_member_keeps_component_out_of_the_unchanged_set(self):
        # Same membership as baseline, but a member's body changed -> still "changed".
        c = _pm_component("C", "3", {"c.py": [_pm_method("c.c1")]})
        root = AnalysisInsights(description="root", components=[c], components_relations=[])
        baseline = _capture_membership_baseline(root, {})
        c.description = "reworded because body changed"

        unchanged = _restore_unchanged_metadata(root, {}, baseline, {"c.c1"})

        self.assertEqual(unchanged, set())
        self.assertEqual(c.description, "reworded because body changed")

    def test_relations_between_unchanged_components_carried_over(self):
        a = _pm_component("A", "1", {"a.py": [_pm_method("a.a1")]})
        b = _pm_component("B", "2", {"b.py": [_pm_method("b.b1")]})
        c = _pm_component("C", "3", {"c.py": [_pm_method("c.c1")]})
        baseline_ab = Relation(relation="uses", src_name="A", dst_name="B", evidence="baseline")
        baseline_ac = Relation(relation="calls", src_name="A", dst_name="C", evidence="baseline")
        root = AnalysisInsights(
            description="root", components=[a, b, c], components_relations=[baseline_ab, baseline_ac]
        )
        baseline = _capture_membership_baseline(root, {})

        # A different component (C) changed; the re-partition reshuffled B's method into A.
        a.file_methods = [
            FileMethodGroup(file_path="a.py", methods=[_pm_method("a.a1")]),
            FileMethodGroup(file_path="b.py", methods=[_pm_method("b.b1")]),
        ]
        b.file_methods = []
        _restore_unchanged_membership(root, {}, baseline, {"c.c1"}, set())
        unchanged = _restore_unchanged_metadata(root, {}, baseline, {"c.c1"})
        # This is exactly what generate_analysis_incremental does to the refresh set.
        changed_ids = {"1", "2", "3"} - unchanged
        self.assertEqual(changed_ids, {"3"})

        regenerated = [
            Relation(relation="reworded", src_name="A", dst_name="B", evidence="llm"),
            Relation(relation="reworded", src_name="A", dst_name="C", evidence="llm"),
        ]
        merged = _preserve_unchanged_relations(root, [baseline_ab, baseline_ac], regenerated, changed_ids)
        by_pair = {(rel.src_name, rel.dst_name): rel for rel in merged}

        self.assertEqual(_pm_owned(a), {("a.py", "a.a1")})
        self.assertEqual(_pm_owned(b), {("b.py", "b.b1")})
        self.assertEqual(by_pair[("A", "B")].evidence, "baseline")
        self.assertEqual(by_pair[("A", "C")].evidence, "llm")

    def test_new_component_methods_are_not_pinned_away(self):
        # A deliberate planner split moves an unchanged method into a fresh component; the
        # protection keeps it there instead of yanking it back to its old owner.
        b = _pm_component("B", "2", {"b.py": [_pm_method("b.b1"), _pm_method("b.b2")]})
        root = AnalysisInsights(description="root", components=[b], components_relations=[])
        baseline = _capture_membership_baseline(root, {})

        new_component = _pm_component("N", "4", {"b.py": [_pm_method("b.b2")]})
        b.file_methods = [FileMethodGroup(file_path="b.py", methods=[_pm_method("b.b1")])]
        root.components.append(new_component)

        _restore_unchanged_membership(root, {}, baseline, set(), {"4"})

        self.assertEqual(_pm_owned(new_component), {("b.py", "b.b2")})
        self.assertEqual(_pm_owned(b), {("b.py", "b.b1")})

    def test_membership_restore_is_scoped_per_sub_analysis(self):
        # Each expanded scope pins against its own baseline owners, independently.
        parent = _pm_component("Parent", "1", {"p.py": [_pm_method("p.a"), _pm_method("p.b")]})
        child_x = _pm_component("X", "1.1", {"p.py": [_pm_method("p.a")]})
        child_y = _pm_component("Y", "1.2", {"p.py": [_pm_method("p.b")]})
        root = AnalysisInsights(description="root", components=[parent], components_relations=[])
        sub = AnalysisInsights(description="sub", components=[child_x, child_y], components_relations=[])
        baseline = _capture_membership_baseline(root, {"1": sub})

        # Sub-scope re-partition wrongly merged both methods under X.
        child_x.file_methods = [FileMethodGroup(file_path="p.py", methods=[_pm_method("p.a"), _pm_method("p.b")])]
        child_y.file_methods = []

        _restore_unchanged_membership(root, {"1": sub}, baseline, set(), set())

        self.assertEqual(_pm_owned(child_x), {("p.py", "p.a")})
        self.assertEqual(_pm_owned(child_y), {("p.py", "p.b")})


def _pm_child_map(scope: AnalysisInsights) -> dict[str, str]:
    """qualified_name -> owning child component_id, across one scope."""
    return {
        method.qualified_name: component.component_id
        for component in scope.components
        for group in component.file_methods
        for method in group.methods
    }


class TestUnchangedSubtreePreservation(unittest.TestCase):
    """A component with no changed member keeps its whole sub-component structure verbatim."""

    def test_unchanged_subtree_child_mapping_preserved_verbatim(self):
        # Only X changed. Y's top-level ownership is intact, but its child scope got
        # reshuffled (a child even vanished) the way a re-partition/reconcile can. Restoring
        # the subtree must reinstate the exact method->child map, not merely top ownership.
        x = _pm_component("X", "1", {"x.py": [_pm_method("x.x1")]})
        y = _pm_component("Y", "2", {"p.py": [_pm_method("y.a"), _pm_method("y.b")]})
        root = AnalysisInsights(description="root", components=[x, y], components_relations=[])
        y1 = _pm_component("Y1", "2.1", {"p.py": [_pm_method("y.a")]})
        y2 = _pm_component("Y2", "2.2", {"p.py": [_pm_method("y.b")]})
        y_scope = AnalysisInsights(description="Y", components=[y1, y2], components_relations=[])
        sub_analyses = {"2": y_scope}
        baseline = _capture_membership_baseline(root, sub_analyses)

        # The re-partition collapsed Y's child scope: both methods under Y1, Y2 gone. This is
        # drift that membership-pinning alone can't undo — Y2's baseline owner no longer exists.
        y1.file_methods = [FileMethodGroup(file_path="p.py", methods=[_pm_method("y.a"), _pm_method("y.b")])]
        y_scope.components = [y1]

        preserved = _restore_unchanged_subtrees(root, sub_analyses, baseline, {"x.x1"}, set())

        self.assertIn("2", preserved)
        self.assertEqual(_pm_child_map(sub_analyses["2"]), {"y.a": "2.1", "y.b": "2.2"})

    def test_changed_component_subtree_is_not_frozen(self):
        # X genuinely changed; its re-partition stands, not the baseline.
        x = _pm_component("X", "1", {"x.py": [_pm_method("x.a"), _pm_method("x.b")]})
        root = AnalysisInsights(description="root", components=[x], components_relations=[])
        x1 = _pm_component("X1", "1.1", {"x.py": [_pm_method("x.a")]})
        x2 = _pm_component("X2", "1.2", {"x.py": [_pm_method("x.b")]})
        x_scope = AnalysisInsights(description="X", components=[x1, x2], components_relations=[])
        sub_analyses = {"1": x_scope}
        baseline = _capture_membership_baseline(root, sub_analyses)

        x1.file_methods = [FileMethodGroup(file_path="x.py", methods=[_pm_method("x.a"), _pm_method("x.b")])]
        x_scope.components = [x1]

        preserved = _restore_unchanged_subtrees(root, sub_analyses, baseline, {"x.b"}, set())

        self.assertNotIn("1", preserved)
        self.assertEqual([c.component_id for c in sub_analyses["1"].components], ["1.1"])

    def test_new_component_in_subtree_blocks_freeze(self):
        # A planner split created a new child inside Y; freezing Y verbatim would delete it.
        y = _pm_component("Y", "2", {"p.py": [_pm_method("y.a"), _pm_method("y.b")]})
        root = AnalysisInsights(description="root", components=[y], components_relations=[])
        y1 = _pm_component("Y1", "2.1", {"p.py": [_pm_method("y.a"), _pm_method("y.b")]})
        y_scope = AnalysisInsights(description="Y", components=[y1], components_relations=[])
        sub_analyses = {"2": y_scope}
        baseline = _capture_membership_baseline(root, sub_analyses)

        new_child = _pm_component("Y2", "2.2", {"p.py": [_pm_method("y.b")]})
        y1.file_methods = [FileMethodGroup(file_path="p.py", methods=[_pm_method("y.a")])]
        y_scope.components = [y1, new_child]

        preserved = _restore_unchanged_subtrees(root, sub_analyses, baseline, set(), {"2.2"})

        self.assertNotIn("2", preserved)
        self.assertEqual({c.component_id for c in sub_analyses["2"].components}, {"2.1", "2.2"})

    def test_nested_grandchild_scope_preserved(self):
        # A change nowhere near Y freezes its entire depth, grandchild scope included.
        y = _pm_component("Y", "2", {"p.py": [_pm_method("y.a"), _pm_method("y.b")]})
        root = AnalysisInsights(description="root", components=[y], components_relations=[])
        y1 = _pm_component("Y1", "2.1", {"p.py": [_pm_method("y.a"), _pm_method("y.b")]})
        y_scope = AnalysisInsights(description="Y", components=[y1], components_relations=[])
        g1 = _pm_component("G1", "2.1.1", {"p.py": [_pm_method("y.a")]})
        g2 = _pm_component("G2", "2.1.2", {"p.py": [_pm_method("y.b")]})
        y1_scope = AnalysisInsights(description="Y1", components=[g1, g2], components_relations=[])
        sub_analyses = {"2": y_scope, "2.1": y1_scope}
        baseline = _capture_membership_baseline(root, sub_analyses)

        # Deep drift: both grandchild methods collapse under G1.
        g1.file_methods = [FileMethodGroup(file_path="p.py", methods=[_pm_method("y.a"), _pm_method("y.b")])]
        y1_scope.components = [g1]

        preserved = _restore_unchanged_subtrees(root, sub_analyses, baseline, set(), set())

        self.assertEqual(preserved & {"2", "2.1", "2.1.1", "2.1.2"}, {"2", "2.1"})
        self.assertEqual(_pm_child_map(sub_analyses["2.1"]), {"y.a": "2.1.1", "y.b": "2.1.2"})


def _pm_relation(src_id: str, dst_id: str, label: str) -> Relation:
    return Relation(relation=label, src_name=f"C{src_id}", dst_name=f"C{dst_id}", src_id=src_id, dst_id=dst_id)


class TestGlobalRelationPreservation(unittest.TestCase):
    """The save-time global relation rebuild carries edges between two untouched components."""

    def _gen(self) -> DiagramGenerator:
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        repo = Path(temp_dir) / "repo"
        repo.mkdir()
        gen = DiagramGenerator(
            repo_location=repo,
            temp_folder=repo,
            repo_name="repo",
            output_dir=repo,
            depth_level=2,
            run_id="rid",
            log_path="repo/log",
        )
        gen.static_analysis = Mock()
        gen.static_analysis.get_languages.return_value = []
        return gen

    @patch("diagram_analysis.diagram_generator.build_global_relations")
    def test_rebuild_preserves_unchanged_pair_label_and_rederives_changed(self, mock_build):
        # Only X (1) changed; Y (2) and Z (3) did not.
        x = _pm_component("X", "1", {"x.py": [_pm_method("x.m1")]})
        y = _pm_component("Y", "2", {"y.py": [_pm_method("y.m1")]})
        z = _pm_component("Z", "3", {"z.py": [_pm_method("z.m1")]})
        root = AnalysisInsights(description="root", components=[x, y, z], components_relations=[])

        gen = self._gen()
        gen._baseline_component_ids = {"1", "2", "3"}
        gen._changed_members = {"x.m1"}
        gen._baseline_global_relations = {
            ("1", "2"): _pm_relation("1", "2", "X calls Y"),
            ("2", "3"): _pm_relation("2", "3", "Y observes Z"),  # between two unchanged components
            ("1", "3"): _pm_relation("1", "3", "X uses Z"),
        }
        # The wholesale rebuild relabels every edge with a fresh LLM description.
        mock_build.return_value = [
            _pm_relation("1", "2", "reworded X-Y"),
            _pm_relation("2", "3", "reworded Y-Z"),
            _pm_relation("1", "3", "reworded X-Z"),
        ]

        result = gen.rebuild_global_relations(root, {})
        by_pair = {(rel.src_id, rel.dst_id): rel.relation for rel in result}

        self.assertEqual(by_pair[("2", "3")], "Y observes Z")  # baseline label kept verbatim
        self.assertEqual(by_pair[("1", "2")], "reworded X-Y")  # touches X -> re-derived
        self.assertEqual(by_pair[("1", "3")], "reworded X-Z")  # touches X -> re-derived
        self.assertIs(result, root.components_relations)

    @patch("diagram_analysis.diagram_generator.build_global_relations")
    def test_full_analysis_rebuilds_every_relation(self, mock_build):
        # No baseline snapshot => full run: the rebuild wins wholesale even for stable pairs.
        y = _pm_component("Y", "2", {"y.py": [_pm_method("y.m1")]})
        z = _pm_component("Z", "3", {"z.py": [_pm_method("z.m1")]})
        root = AnalysisInsights(description="root", components=[y, z], components_relations=[])
        gen = self._gen()
        mock_build.return_value = [_pm_relation("2", "3", "reworded Y-Z")]

        result = gen.rebuild_global_relations(root, {})

        self.assertEqual([rel.relation for rel in result], ["reworded Y-Z"])

    def test_changed_ids_include_owner_ancestors_and_new_components(self):
        parent = _pm_component("Parent", "1", {"p.py": [_pm_method("p.deep")]})
        sibling = _pm_component("Sibling", "2", {"s.py": [_pm_method("s.stable")]})
        fresh = _pm_component("Fresh", "3", {"f.py": [_pm_method("f.m")]})
        child = _pm_component("Child", "1.1", {"p.py": [_pm_method("p.deep")]})
        root = AnalysisInsights(description="root", components=[parent, sibling, fresh], components_relations=[])
        sub = {"1": AnalysisInsights(description="sub", components=[child], components_relations=[])}

        changed = _incremental_changed_component_ids(
            root, sub, baseline_component_ids={"1", "1.1", "2"}, changed_members={"p.deep"}
        )

        # Owner of the changed member (1.1), its ancestor (1), and the new component (3).
        self.assertEqual(changed, {"1", "1.1", "3"})

    def test_structural_drift_between_unchanged_components_is_squashed(self):
        baseline = {
            ("2", "3"): _pm_relation("2", "3", "kept edge"),
            ("2", "4"): _pm_relation("2", "4", "restored edge"),  # rebuild wrongly dropped it
        }
        rebuilt = [
            _pm_relation("2", "3", "reworded"),  # unchanged pair with a baseline -> use baseline
            _pm_relation("3", "4", "spurious new"),  # unchanged pair, no baseline -> drop
        ]

        merged = _preserve_unchanged_global_relations(
            rebuilt, baseline, changed_component_ids=set(), live_ids={"2", "3", "4"}
        )

        pairs = {(rel.src_id, rel.dst_id): rel.relation for rel in merged}
        self.assertEqual(pairs, {("2", "3"): "kept edge", ("2", "4"): "restored edge"})

    def test_baseline_edge_to_a_removed_component_is_not_restored(self):
        baseline = {("2", "9"): _pm_relation("2", "9", "to removed")}

        merged = _preserve_unchanged_global_relations([], baseline, changed_component_ids=set(), live_ids={"2"})

        self.assertEqual(merged, [])


if __name__ == "__main__":
    unittest.main()
