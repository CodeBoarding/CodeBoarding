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
    FileEntry,
    FileMethodGroup,
    MethodEntry,
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
from agents.incremental_results import ScopeUpdateResult
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
from diagram_analysis.cluster_delta import ClusterMemberDelta, ClusterRef, LanguageStructuralDiff, StructuralClusterDiff
from diagram_analysis.diagram_generator import DiagramGenerator, _component_depth, _component_expansion_seeds
from diagram_analysis.exceptions import IncrementalCacheMissingError
from diagram_analysis.version import Version
from repo_utils.change_detector import ChangeSet
from static_analyzer.analysis_cache import StaticAnalysisCache
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Language, NodeType
from static_analyzer.graph import CallGraph, ClusterResult
from static_analyzer.node import Node


class TestVersion(unittest.TestCase):
    def test_version_creation(self):
        # Test creating a Version instance
        version = Version(commit_hash="abc123", code_boarding_version="1.0.0")

        self.assertEqual(version.commit_hash, "abc123")
        self.assertEqual(version.code_boarding_version, "1.0.0")

    def test_version_model_dump(self):
        # Test model serialization
        version = Version(commit_hash="def456", code_boarding_version="2.0.0")
        data = version.model_dump()

        self.assertEqual(data["commit_hash"], "def456")
        self.assertEqual(data["code_boarding_version"], "2.0.0")

    def test_version_model_dump_json(self):
        # Test JSON serialization
        version = Version(commit_hash="ghi789", code_boarding_version="3.0.0")
        json_str = version.model_dump_json()

        data = json.loads(json_str)
        self.assertEqual(data["commit_hash"], "ghi789")
        self.assertEqual(data["code_boarding_version"], "3.0.0")


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

        data = json.loads(build_unified_analysis_json(self.analysis, [], "repo", self.repo_dir))
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

        data = json.loads(build_unified_analysis_json(self.analysis, [], "repo", self.repo_dir))
        parsed, _ = parse_unified_analysis(data)

        edge = parsed.components_relations[0].key_edges[0]
        self.assertEqual(edge.source.qualified_name, "component1.dispatch")
        self.assertEqual(edge.target.reference_file, "component2.py")
        self.assertEqual(edge.description, "dispatches through registry")
        self.assertEqual(
            [site.model_dump() for site in edge.call_sites], [{"line": 14, "column": 6}, {"line": 16, "column": 10}]
        )

    def test_unified_analysis_parse_skips_edges_missing_from_methods_index(self):
        data = json.loads(build_unified_analysis_json(self.analysis, [], "repo", self.repo_dir))
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
        self.assertEqual(parsed.components_relations[0].key_edges, [])

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

    @patch("diagram_analysis.diagram_generator.ProjectScanner")
    @patch("diagram_analysis.diagram_generator.get_static_analysis")
    @patch("diagram_analysis.diagram_generator.initialize_llms")
    @patch("diagram_analysis.diagram_generator.MetaAgent")
    @patch("diagram_analysis.diagram_generator.DetailsAgent")
    @patch("diagram_analysis.diagram_generator.AbstractionAgent")
    @patch("diagram_analysis.diagram_generator.get_git_commit_hash")
    def test_pre_analysis(
        self,
        mock_git_hash,
        mock_abstraction,
        mock_details,
        mock_meta,
        mock_initialize_llms,
        mock_get_static_analysis,
        mock_scanner,
    ):
        # Test pre_analysis method
        mock_git_hash.return_value = "abc123"
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

        gen.pre_analysis()

        # Verify agents were created
        self.assertIsNotNone(gen.meta_agent)
        self.assertIsNotNone(gen.details_agent)
        self.assertIsNotNone(gen.abstraction_agent)
        mock_meta_instance.analyze_project_metadata.assert_called_once_with(skip_cache=False)
        # Note: planner is now a module function, not an agent instance

        # Verify version file was created
        version_file = self.output_dir / "codeboarding_version.json"
        self.assertTrue(version_file.exists())

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
            sub_analyses,
            file_coverage_summary=None,
            commit_hash="",
            snapshot_commit=None,
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

    @patch("diagram_analysis.diagram_generator.get_git_commit_hash", return_value="abc123")
    @patch("diagram_analysis.diagram_generator.save_analysis")
    @patch("diagram_analysis.diagram_generator.get_expandable_components")
    def test_generate_subcomponents_respects_absolute_depth(
        self,
        mock_get_expandable_components,
        mock_save_analysis,
        _mock_git_hash,
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

    @patch("diagram_analysis.diagram_generator.get_git_commit_hash", return_value="abc123")
    @patch("diagram_analysis.diagram_generator.save_analysis")
    @patch("diagram_analysis.diagram_generator.prune_empty_components", return_value=set())
    @patch("diagram_analysis.diagram_generator._build_scope_incremental_inputs")
    @patch("diagram_analysis.diagram_generator.structural_diff_from_delta")
    @patch("diagram_analysis.diagram_generator.IncrementalPlanningAgent")
    @patch("diagram_analysis.diagram_generator.IncrementalAgent")
    @patch("diagram_analysis.diagram_generator.initialize_llms", return_value=(Mock(), Mock()))
    @patch("diagram_analysis.diagram_generator.compute_cluster_delta")
    @patch("diagram_analysis.diagram_generator.snapshot_from_static_analysis")
    def test_incremental_refresh_updates_existing_parent_scope(
        self,
        mock_snapshot,
        mock_delta,
        _mock_llms,
        _mock_incremental_agent,
        mock_planning_agent,
        _mock_structural_diff,
        mock_build_scope_inputs,
        _mock_prune,
        mock_save_analysis,
        _mock_git_hash,
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
        gen.abstraction_agent = Mock()
        gen.abstraction_agent.build_files_index.return_value = {}
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
            ScopeUpdateResult(refresh_ids={"1"}, new_component_ids=set()),
            ScopeUpdateResult(refresh_ids={"1.1"}, new_component_ids=set()),
        ]
        mock_save_analysis.return_value = self.output_dir / "analysis.json"

        gen.generate_analysis_incremental(root_analysis, sub_analyses)

        mock_snapshot.assert_called_once_with(base_static_analysis)
        mock_planning_agent.return_value.decide_scope_update.assert_called_once_with("root", root_analysis, root_diff)
        _mock_incremental_agent.return_value.update_scope.assert_called_once()
        mock_build_scope_inputs.assert_called_once_with(
            root_component,
            "1",
            _mock_incremental_agent.return_value,
            gen.changes,
            gen.repo_location,
        )
        gen._generate_subcomponents.assert_not_called()
        self.assertEqual(sub_analyses["1"].components[0].name, "Stable Child")

    @patch("diagram_analysis.diagram_generator.get_git_commit_hash", return_value="abc123")
    @patch("diagram_analysis.diagram_generator.save_analysis")
    @patch("diagram_analysis.diagram_generator.prune_empty_components", return_value=set())
    @patch("diagram_analysis.diagram_generator._build_scope_incremental_inputs")
    @patch("diagram_analysis.diagram_generator.structural_diff_from_delta")
    @patch("diagram_analysis.diagram_generator.IncrementalPlanningAgent")
    @patch("diagram_analysis.diagram_generator.IncrementalAgent")
    @patch("diagram_analysis.diagram_generator.initialize_llms", return_value=(Mock(), Mock()))
    @patch("diagram_analysis.diagram_generator.compute_cluster_delta")
    @patch("diagram_analysis.diagram_generator.snapshot_from_static_analysis")
    def test_incremental_refresh_skips_child_scope_when_local_diff_is_empty(
        self,
        mock_snapshot,
        mock_delta,
        _mock_llms,
        _mock_incremental_agent,
        mock_planning_agent,
        _mock_structural_diff,
        mock_build_scope_inputs,
        _mock_prune,
        mock_save_analysis,
        _mock_git_hash,
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
        gen.abstraction_agent = Mock()
        gen.abstraction_agent.build_files_index.return_value = {}
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
            refresh_ids={"1"}, new_component_ids=set()
        )
        mock_build_scope_inputs.return_value = ({}, StructuralClusterDiff())
        mock_save_analysis.return_value = self.output_dir / "analysis.json"

        gen.generate_analysis_incremental(root_analysis, sub_analyses)

        self.assertEqual(mock_planning_agent.return_value.decide_scope_update.call_count, 1)
        self.assertEqual(_mock_incremental_agent.return_value.update_scope.call_count, 1)
        gen._generate_subcomponents.assert_not_called()

    @patch("diagram_analysis.diagram_generator.get_git_commit_hash", return_value="abc123")
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
        _mock_git_hash,
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
        gen.abstraction_agent = Mock()
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

        gen.generate_analysis_incremental(root_analysis, sub_analyses)

        mock_prune.assert_not_called()
        self.assertEqual(sub_analyses["1.1"].components[0].name, "Stable Leaf")

    def test_persist_static_analysis_artifact_saves_cluster_cache_without_injected_analyzer(self):
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
        cfg._cluster_cache = ClusterResult(
            clusters={1: {"test.fn"}},
            cluster_to_files={1: {str(self.repo_location / "test.py")}},
            file_to_clusters={str(self.repo_location / "test.py"): {1}},
            strategy="test",
        )
        results = StaticAnalysisResults()
        results.add_cfg(Language.PYTHON, cfg)
        gen.static_analysis = results

        gen._persist_static_analysis_artifact()

        loaded = StaticAnalysisCache(self.output_dir, self.repo_location).load_with_sha()
        self.assertIsNotNone(loaded)
        if loaded is None:
            return
        loaded_results, cached_sha = loaded
        self.assertEqual(cached_sha, "sha-current")
        self.assertIsNotNone(loaded_results.get_cfg(Language.PYTHON)._cluster_cache)

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
    @patch("diagram_analysis.diagram_generator.get_git_commit_hash", return_value="abc")
    def test_finalize_and_save_persists_side_artifacts_by_default(self, _hash, mock_save):
        mock_save.return_value = self.output_dir / "analysis.json"
        gen = self._finalize_gen()
        analysis = AnalysisInsights(description="d", components=[], components_relations=[])

        gen.finalize_and_save(analysis, {})

        gen._write_file_coverage.assert_called_once()
        gen._persist_static_analysis_artifact.assert_called_once()

    @patch("diagram_analysis.diagram_generator.save_analysis")
    @patch("diagram_analysis.diagram_generator.get_git_commit_hash", return_value="abc")
    def test_finalize_and_save_skips_side_artifacts_for_partial(self, _hash, mock_save):
        """Component expansion (partial) must not rewrite file_coverage.json or touch
        the static-analysis cache/SHA tag — that would regress the next incremental run."""
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


if __name__ == "__main__":
    unittest.main()
