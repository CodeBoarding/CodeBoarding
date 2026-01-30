"""Tests for the AnalysisCacheManager class."""

import json
import tempfile
import shutil
import unittest
from pathlib import Path

from static_analyzer.analysis_cache import AnalysisCacheManager
from static_analyzer.graph import CallGraph, Node


class TestAnalysisCacheManager(unittest.TestCase):
    """Tests for AnalysisCacheManager save/load functionality."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cache_path = Path(self.temp_dir) / "test_cache.json"
        self.cache_manager = AnalysisCacheManager()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_sample_analysis_result(self) -> dict:
        """Create a sample analysis result for testing."""
        call_graph = CallGraph()

        # Add some nodes
        node1 = Node("module.function1", 6, "/path/to/file1.py", 10, 20)
        node2 = Node("module.function2", 6, "/path/to/file2.py", 30, 40)
        call_graph.add_node(node1)
        call_graph.add_node(node2)

        # Add an edge
        call_graph.add_edge("module.function1", "module.function2")

        return {
            "call_graph": call_graph,
            "class_hierarchies": {
                "module.TestClass": {
                    "superclasses": ["module.BaseClass"],
                    "subclasses": [],
                    "file_path": "/path/to/file1.py",
                    "line_start": 5,
                    "line_end": 25,
                }
            },
            "package_relations": {
                "module": {
                    "imports": ["other_module"],
                    "imported_by": [],
                    "files": ["/path/to/file1.py", "/path/to/file2.py"],
                }
            },
            "references": [node1, node2],
            "source_files": [Path("/path/to/file1.py"), Path("/path/to/file2.py")],
        }

    def test_save_cache_creates_file(self):
        """save_cache should create a cache file."""
        analysis_result = self._create_sample_analysis_result()

        self.cache_manager.save_cache(self.cache_path, analysis_result, "abc123", 1)

        self.assertTrue(self.cache_path.exists())

    def test_save_cache_creates_directory(self):
        """save_cache should create parent directories if they don't exist."""
        nested_cache_path = Path(self.temp_dir) / "nested" / "dir" / "cache.json"
        analysis_result = self._create_sample_analysis_result()

        self.cache_manager.save_cache(nested_cache_path, analysis_result, "abc123", 1)

        self.assertTrue(nested_cache_path.exists())

    def test_save_and_load_roundtrip(self):
        """Saved analysis should be loadable with same data."""
        original_analysis = self._create_sample_analysis_result()

        # Save
        self.cache_manager.save_cache(self.cache_path, original_analysis, "abc123", 1)

        # Load
        result = self.cache_manager.load_cache(self.cache_path)
        self.assertIsNotNone(result)

        loaded_analysis, commit_hash, iteration_id = result

        # Check metadata
        self.assertEqual(commit_hash, "abc123")
        self.assertEqual(iteration_id, 1)

        # Check call graph
        self.assertEqual(len(loaded_analysis["call_graph"].nodes), 2)
        self.assertEqual(len(loaded_analysis["call_graph"].edges), 1)
        self.assertIn("module.function1", loaded_analysis["call_graph"].nodes)
        self.assertIn("module.function2", loaded_analysis["call_graph"].nodes)

        # Check class hierarchies
        self.assertIn("module.TestClass", loaded_analysis["class_hierarchies"])
        class_info = loaded_analysis["class_hierarchies"]["module.TestClass"]
        self.assertEqual(class_info["superclasses"], ["module.BaseClass"])

        # Check package relations
        self.assertIn("module", loaded_analysis["package_relations"])
        package_info = loaded_analysis["package_relations"]["module"]
        self.assertEqual(package_info["imports"], ["other_module"])

        # Check references
        self.assertEqual(len(loaded_analysis["references"]), 2)

        # Check source files
        self.assertEqual(len(loaded_analysis["source_files"]), 2)

    def test_load_cache_returns_none_for_missing_file(self):
        """load_cache should return None if cache file doesn't exist."""
        result = self.cache_manager.load_cache(Path("/nonexistent/cache.json"))
        self.assertIsNone(result)

    def test_load_cache_returns_none_for_invalid_json(self):
        """load_cache should return None if cache file contains invalid JSON."""
        self.cache_path.write_text("invalid json content")

        result = self.cache_manager.load_cache(self.cache_path)
        self.assertIsNone(result)

    def test_load_cache_returns_none_for_invalid_structure(self):
        """load_cache should return None if cache file has invalid structure."""
        invalid_cache = {"invalid": "structure"}
        self.cache_path.write_text(json.dumps(invalid_cache))

        result = self.cache_manager.load_cache(self.cache_path)
        self.assertIsNone(result)

    def test_save_cache_validates_input(self):
        """save_cache should validate that analysis_result has required keys."""
        invalid_analysis = {"missing_keys": True}

        with self.assertRaises(ValueError) as context:
            self.cache_manager.save_cache(self.cache_path, invalid_analysis, "abc123", 1)

        self.assertIn("missing required keys", str(context.exception))

    def test_invalidate_files_removes_data_for_changed_files(self):
        """invalidate_files should remove data for specified files."""
        analysis_result = self._create_sample_analysis_result()
        changed_files = {Path("/path/to/file1.py")}

        updated_result = self.cache_manager.invalidate_files(analysis_result, changed_files)

        # Should only have data for file2.py
        remaining_nodes = [node for node in updated_result["call_graph"].nodes.values()]
        self.assertEqual(len(remaining_nodes), 1)
        self.assertEqual(remaining_nodes[0].file_path, "/path/to/file2.py")

        # Should have no edges since the edge connected file1 to file2
        self.assertEqual(len(updated_result["call_graph"].edges), 0)

        # References should only include file2.py
        remaining_refs = [ref for ref in updated_result["references"]]
        self.assertEqual(len(remaining_refs), 1)
        self.assertEqual(remaining_refs[0].file_path, "/path/to/file2.py")

    def test_invalidate_files_validates_no_dangling_references(self):
        """invalidate_files should validate no dangling references remain."""
        analysis_result = self._create_sample_analysis_result()
        changed_files = {Path("/path/to/file1.py")}

        # This should work without raising an exception
        updated_result = self.cache_manager.invalidate_files(analysis_result, changed_files)

        # Verify the validation passed by checking the result is valid
        self.assertIsInstance(updated_result, dict)
        self.assertIn("call_graph", updated_result)

    def test_remove_nodes_for_files(self):
        """remove_nodes_for_files should remove nodes from specified files."""
        call_graph = CallGraph()
        node1 = Node("module.function1", 6, "/path/to/file1.py", 10, 20)
        node2 = Node("module.function2", 6, "/path/to/file2.py", 30, 40)
        call_graph.add_node(node1)
        call_graph.add_node(node2)

        filtered_graph, removed_nodes = self.cache_manager.remove_nodes_for_files(call_graph, {"/path/to/file1.py"})

        self.assertEqual(len(filtered_graph.nodes), 1)
        self.assertIn("module.function2", filtered_graph.nodes)
        self.assertNotIn("module.function1", filtered_graph.nodes)
        self.assertEqual(removed_nodes, {"module.function1"})

    def test_remove_edges_referencing_nodes(self):
        """remove_edges_referencing_nodes should remove edges that reference removed nodes."""
        call_graph = CallGraph()
        node1 = Node("module.function1", 6, "/path/to/file1.py", 10, 20)
        node2 = Node("module.function2", 6, "/path/to/file2.py", 30, 40)
        node3 = Node("module.function3", 6, "/path/to/file3.py", 50, 60)
        call_graph.add_node(node1)
        call_graph.add_node(node2)
        call_graph.add_node(node3)
        call_graph.add_edge("module.function1", "module.function2")
        call_graph.add_edge("module.function2", "module.function3")

        filtered_graph = self.cache_manager.remove_edges_referencing_nodes(call_graph, {"module.function1"})

        # Should have all nodes but only one edge (function2 -> function3)
        self.assertEqual(len(filtered_graph.nodes), 3)
        self.assertEqual(len(filtered_graph.edges), 1)

        # Verify the remaining edge
        remaining_edge = filtered_graph.edges[0]
        self.assertEqual(remaining_edge.get_source(), "module.function2")
        self.assertEqual(remaining_edge.get_destination(), "module.function3")

    def test_remove_class_hierarchies_for_files(self):
        """remove_class_hierarchies_for_files should remove class hierarchies from specified files."""
        class_hierarchies = {
            "module.Class1": {"file_path": "/path/to/file1.py", "superclasses": []},
            "module.Class2": {"file_path": "/path/to/file2.py", "superclasses": []},
            "module.Class3": {"file_path": "/path/to/file1.py", "superclasses": []},
        }

        filtered_hierarchies = self.cache_manager.remove_class_hierarchies_for_files(
            class_hierarchies, {"/path/to/file1.py"}
        )

        self.assertEqual(len(filtered_hierarchies), 1)
        self.assertIn("module.Class2", filtered_hierarchies)
        self.assertNotIn("module.Class1", filtered_hierarchies)
        self.assertNotIn("module.Class3", filtered_hierarchies)

    def test_remove_package_relations_for_files(self):
        """remove_package_relations_for_files should update package relations to exclude specified files."""
        package_relations = {
            "package1": {"files": ["/path/to/file1.py", "/path/to/file2.py"]},
            "package2": {"files": ["/path/to/file1.py"]},
            "package3": {"files": ["/path/to/file3.py"]},
        }

        filtered_relations = self.cache_manager.remove_package_relations_for_files(
            package_relations, {"/path/to/file1.py"}
        )

        # package1 should have only file2.py
        self.assertIn("package1", filtered_relations)
        self.assertEqual(filtered_relations["package1"]["files"], ["/path/to/file2.py"])

        # package2 should be completely removed (no remaining files)
        self.assertNotIn("package2", filtered_relations)

        # package3 should remain unchanged
        self.assertIn("package3", filtered_relations)
        self.assertEqual(filtered_relations["package3"]["files"], ["/path/to/file3.py"])

    def test_remove_references_for_files(self):
        """remove_references_for_files should remove references from specified files."""
        node1 = Node("module.function1", 6, "/path/to/file1.py", 10, 20)
        node2 = Node("module.function2", 6, "/path/to/file2.py", 30, 40)
        references = [node1, node2]

        filtered_references = self.cache_manager.remove_references_for_files(references, {"/path/to/file1.py"})

        self.assertEqual(len(filtered_references), 1)
        self.assertEqual(filtered_references[0].file_path, "/path/to/file2.py")

    def test_identify_analysis_data_for_files(self):
        """identify_analysis_data_for_files should identify all data associated with specified files."""
        analysis_result = self._create_sample_analysis_result()
        file_paths = {Path("/path/to/file1.py")}

        affected_data = self.cache_manager.identify_analysis_data_for_files(analysis_result, file_paths)

        # Check structure
        self.assertIn("nodes", affected_data)
        self.assertIn("edges", affected_data)
        self.assertIn("class_hierarchies", affected_data)
        self.assertIn("package_relations", affected_data)
        self.assertIn("references", affected_data)
        self.assertIn("summary", affected_data)

        # Check content
        self.assertIn("module.function1", affected_data["nodes"])
        self.assertIn("module.TestClass", affected_data["class_hierarchies"])
        self.assertIn("module", affected_data["package_relations"])
        self.assertIn("module.function1", affected_data["references"])

        # Check summary counts
        summary = affected_data["summary"]
        self.assertEqual(summary["node_count"], 1)
        self.assertEqual(summary["class_count"], 1)
        self.assertEqual(summary["package_count"], 1)
        self.assertEqual(summary["reference_count"], 1)

    def test_validate_no_dangling_references_detects_invalid_edges(self):
        """_validate_no_dangling_references should detect edges referencing non-existent nodes."""
        # Create a malformed analysis result with dangling edge references
        call_graph = CallGraph()
        node1 = Node("module.function1", 6, "/path/to/file1.py", 10, 20)
        call_graph.add_node(node1)

        # Manually add an edge that references a non-existent node
        from static_analyzer.graph import Edge

        dangling_node = Node("module.nonexistent", 6, "/path/to/nonexistent.py", 1, 10)
        dangling_edge = Edge(node1, dangling_node)
        call_graph.edges.append(dangling_edge)
        call_graph._edge_set.add(("module.function1", "module.nonexistent"))

        invalid_result = {
            "call_graph": call_graph,
            "class_hierarchies": {},
            "package_relations": {},
            "references": [],
            "source_files": [Path("/path/to/file1.py")],
        }

        with self.assertRaises(ValueError) as context:
            self.cache_manager._validate_no_dangling_references(invalid_result)

        self.assertIn("dangling references", str(context.exception).lower())
        self.assertIn("module.nonexistent", str(context.exception))

    def test_validate_no_dangling_references_detects_invalid_class_files(self):
        """_validate_no_dangling_references should detect class hierarchies referencing non-existent files."""
        invalid_result = {
            "call_graph": CallGraph(),
            "class_hierarchies": {"module.TestClass": {"file_path": "/path/to/nonexistent.py", "superclasses": []}},
            "package_relations": {},
            "references": [],
            "source_files": [Path("/path/to/existing.py")],
        }

        with self.assertRaises(ValueError) as context:
            self.cache_manager._validate_no_dangling_references(invalid_result)

        self.assertIn("dangling references", str(context.exception).lower())
        self.assertIn("nonexistent.py", str(context.exception))

    def test_merge_results_combines_data(self):
        """merge_results should combine cached and new analysis data."""
        # Create cached result with file1
        cached_call_graph = CallGraph()
        cached_node = Node("module.cached_function", 6, "/path/to/cached.py", 10, 20)
        cached_call_graph.add_node(cached_node)

        cached_result = {
            "call_graph": cached_call_graph,
            "class_hierarchies": {"cached.Class": {"file_path": "/path/to/cached.py"}},
            "package_relations": {"cached_pkg": {"files": ["/path/to/cached.py"]}},
            "references": [cached_node],
            "source_files": [Path("/path/to/cached.py")],
        }

        # Create new result with file2
        new_call_graph = CallGraph()
        new_node = Node("module.new_function", 6, "/path/to/new.py", 30, 40)
        new_call_graph.add_node(new_node)

        new_result = {
            "call_graph": new_call_graph,
            "class_hierarchies": {"new.Class": {"file_path": "/path/to/new.py"}},
            "package_relations": {"new_pkg": {"files": ["/path/to/new.py"]}},
            "references": [new_node],
            "source_files": [Path("/path/to/new.py")],
        }

        # Merge
        merged_result = self.cache_manager.merge_results(cached_result, new_result)

        # Should have both nodes
        self.assertEqual(len(merged_result["call_graph"].nodes), 2)
        self.assertIn("module.cached_function", merged_result["call_graph"].nodes)
        self.assertIn("module.new_function", merged_result["call_graph"].nodes)

        # Should have both class hierarchies
        self.assertEqual(len(merged_result["class_hierarchies"]), 2)
        self.assertIn("cached.Class", merged_result["class_hierarchies"])
        self.assertIn("new.Class", merged_result["class_hierarchies"])

        # Should have both package relations
        self.assertEqual(len(merged_result["package_relations"]), 2)
        self.assertIn("cached_pkg", merged_result["package_relations"])
        self.assertIn("new_pkg", merged_result["package_relations"])

        # Should have both references
        self.assertEqual(len(merged_result["references"]), 2)

        # Should have both source files
        self.assertEqual(len(merged_result["source_files"]), 2)


if __name__ == "__main__":
    unittest.main()
