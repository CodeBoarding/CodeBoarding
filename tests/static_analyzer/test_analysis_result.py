import unittest

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import Node, CallGraph, Edge


class TestStaticAnalysisResults(unittest.TestCase):

    def setUp(self):
        self.results = StaticAnalysisResults()

    def test_add_and_get_languages(self):
        # Test language tracking
        self.assertEqual(self.results.get_languages(), [])

        self.results.add_class_hierarchy("python", {})
        self.assertIn("python", self.results.get_languages())

        self.results.add_cfg("typescript", CallGraph())
        self.assertIn("typescript", self.results.get_languages())
        self.assertEqual(len(self.results.get_languages()), 2)

    def test_add_and_get_hierarchy(self):
        # Test class hierarchy storage and retrieval
        hierarchy = {
            "MyClass": {
                "superclasses": ["BaseClass"],
                "subclasses": [],
                "file_path": "test.py",
                "line_start": 1,
                "line_end": 10,
            }
        }
        self.results.add_class_hierarchy("python", hierarchy)

        retrieved = self.results.get_hierarchy("python")
        self.assertEqual(retrieved, hierarchy)

    def test_get_hierarchy_not_found(self):
        # Test error when hierarchy not found
        with self.assertRaises(ValueError) as context:
            self.results.get_hierarchy("python")
        self.assertIn("not found", str(context.exception))

    def test_add_and_get_cfg(self):
        # Test CFG storage and retrieval
        cfg = CallGraph()
        node1 = Node("test.func1", 12, "test.py", 1, 5)
        node2 = Node("test.func2", 12, "test.py", 6, 10)
        cfg.add_node(node1)
        cfg.add_node(node2)
        cfg.add_edge("test.func1", "test.func2")

        self.results.add_cfg("python", cfg)
        retrieved = self.results.get_cfg("python")

        self.assertEqual(len(retrieved.nodes), 2)
        self.assertEqual(len(retrieved.edges), 1)

    def test_get_cfg_not_found(self):
        # Test error when CFG not found
        with self.assertRaises(ValueError) as context:
            self.results.get_cfg("python")
        self.assertIn("not found", str(context.exception))

    def test_add_and_get_package_dependencies(self):
        # Test package dependencies storage
        deps = {"mypackage": {"imports": ["requests"], "imported_by": ["main"]}}
        self.results.add_package_dependencies("python", deps)

        retrieved = self.results.get_package_dependencies("python")
        self.assertEqual(retrieved, deps)

    def test_get_package_dependencies_not_found(self):
        # Test error when dependencies not found
        with self.assertRaises(ValueError) as context:
            self.results.get_package_dependencies("python")
        self.assertIn("not found", str(context.exception))

    def test_add_and_get_references(self):
        # Test source code references with case-insensitive lookup
        node1 = Node("MyClass.method", 6, "test.py", 1, 5)
        node2 = Node("utils.helper", 12, "utils.py", 10, 15)
        self.results.add_references("python", [node1, node2])

        # Test case-insensitive retrieval
        retrieved = self.results.get_reference("python", "myclass.method")
        self.assertEqual(retrieved.fully_qualified_name, "MyClass.method")

        retrieved2 = self.results.get_reference("python", "UTILS.HELPER")
        self.assertEqual(retrieved2.fully_qualified_name, "utils.helper")

    def test_get_reference_not_found(self):
        # Test error when reference not found
        node = Node("MyClass.method", 6, "test.py", 1, 5)
        self.results.add_references("python", [node])

        with self.assertRaises(ValueError) as context:
            self.results.get_reference("python", "nonexistent")
        self.assertIn("not found", str(context.exception))

    def test_get_reference_file_path_error(self):
        # Test file path detection
        node = Node("mymodule.file.Class", 5, "mymodule/file.py", 1, 5)
        self.results.add_references("python", [node])

        with self.assertRaises(FileExistsError) as context:
            self.results.get_reference("python", "mymodule.file")
        self.assertIn("file path", str(context.exception))

    def test_get_loose_reference(self):
        # Test loose reference matching
        node = Node("mypackage.module.MyClass.method", 6, "test.py", 1, 5)
        self.results.add_references("python", [node])

        # Should match by suffix
        message, retrieved = self.results.get_loose_reference("python", "myclass.method")
        self.assertIsNotNone(retrieved)
        assert retrieved is not None
        self.assertEqual(retrieved.fully_qualified_name, "mypackage.module.MyClass.method")

    def test_get_loose_reference_unique_substring(self):
        # Test loose reference with unique substring
        node = Node("mypackage.unique_function", 12, "test.py", 1, 5)
        self.results.add_references("python", [node])

        message, retrieved = self.results.get_loose_reference("python", "unique")
        self.assertIsNotNone(retrieved)
        assert retrieved is not None
        self.assertEqual(retrieved.fully_qualified_name, "mypackage.unique_function")

    def test_get_loose_reference_not_found(self):
        # Test when no loose match found
        node = Node("mypackage.module.Class", 5, "test.py", 1, 5)
        self.results.add_references("python", [node])

        message, retrieved = self.results.get_loose_reference("python", "nonexistent")
        self.assertIsNone(retrieved)

    def test_add_and_get_source_files(self):
        # Test source file tracking
        files = ["src/main.py", "src/utils.py", "tests/test_main.py"]
        self.results.add_source_files("python", files)

        retrieved = self.results.get_source_files("python")
        self.assertEqual(retrieved, files)

    def test_get_source_files_empty(self):
        # Test when no source files exist
        files = self.results.get_source_files("python")
        self.assertEqual(files, [])

    def test_get_all_source_files(self):
        # Test retrieving all source files across languages
        self.results.add_source_files("python", ["main.py", "utils.py"])
        self.results.add_source_files("typescript", ["index.ts", "app.ts"])

        all_files = self.results.get_all_source_files()
        self.assertEqual(len(all_files), 4)
        self.assertIn("main.py", all_files)
        self.assertIn("index.ts", all_files)

    def test_multiple_language_isolation(self):
        # Test that different languages maintain separate data
        self.results.add_class_hierarchy("python", {"PythonClass": {}})
        self.results.add_class_hierarchy("typescript", {"TypeScriptClass": {}})

        python_hierarchy = self.results.get_hierarchy("python")
        ts_hierarchy = self.results.get_hierarchy("typescript")

        self.assertIn("PythonClass", python_hierarchy)
        self.assertNotIn("PythonClass", ts_hierarchy)
        self.assertIn("TypeScriptClass", ts_hierarchy)
        self.assertNotIn("TypeScriptClass", python_hierarchy)
