import unittest

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Node
from static_analyzer.graph import CallGraph


class TestStaticAnalysisResults(unittest.TestCase):
    def setUp(self):
        self.results = StaticAnalysisResults()

    def test_language_tracking(self):
        self.assertEqual(self.results.get_languages(), [])
        self.results.add_class_hierarchy("python", {})
        self.assertIn("python", self.results.get_languages())
        self.results.add_cfg("typescript", CallGraph())
        self.assertIn("typescript", self.results.get_languages())
        self.assertEqual(len(self.results.get_languages()), 2)

    def test_hierarchy_storage_and_retrieval(self):
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

    def test_cfg_storage_and_retrieval(self):
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

    def test_package_dependencies_storage(self):
        deps = {"mypackage": {"imports": ["requests"], "imported_by": ["main"]}}
        self.results.add_package_dependencies("python", deps)
        retrieved = self.results.get_package_dependencies("python")
        self.assertEqual(retrieved, deps)

    def test_references_case_insensitive_lookup(self):
        node1 = Node("MyClass.method", 6, "test.py", 1, 5)
        node2 = Node("utils.helper", 12, "utils.py", 10, 15)
        self.results.add_references("python", [node1, node2])
        retrieved = self.results.get_reference("python", "myclass.method")
        self.assertEqual(retrieved.fully_qualified_name, "MyClass.method")
        retrieved2 = self.results.get_reference("python", "UTILS.HELPER")
        self.assertEqual(retrieved2.fully_qualified_name, "utils.helper")

    def test_missing_data_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.results.get_hierarchy("nonexistent")
        with self.assertRaises(ValueError):
            self.results.get_cfg("nonexistent")
        with self.assertRaises(ValueError):
            self.results.get_package_dependencies("nonexistent")
        with self.assertRaises(ValueError):
            self.results.get_reference("nonexistent", "any")

    def test_reference_file_path_error(self):
        node = Node("mymodule.file.Class", 5, "mymodule/file.py", 1, 5)
        self.results.add_references("python", [node])
        with self.assertRaises(FileExistsError):
            self.results.get_reference("python", "mymodule.file")

    def test_loose_reference_matching(self):
        node = Node("mypackage.module.MyClass.method", 6, "test.py", 1, 5)
        self.results.add_references("python", [node])
        message, retrieved = self.results.get_loose_reference("python", "myclass.method")
        self.assertIsNotNone(retrieved)
        assert retrieved is not None
        self.assertEqual(retrieved.fully_qualified_name, "mypackage.module.MyClass.method")

    def test_loose_reference_unique_substring(self):
        node = Node("mypackage.unique_function", 12, "test.py", 1, 5)
        self.results.add_references("python", [node])
        message, retrieved = self.results.get_loose_reference("python", "unique")
        self.assertIsNotNone(retrieved)
        assert retrieved is not None
        self.assertEqual(retrieved.fully_qualified_name, "mypackage.unique_function")

    def test_loose_reference_not_found(self):
        node = Node("mypackage.module.Class", 5, "test.py", 1, 5)
        self.results.add_references("python", [node])
        message, retrieved = self.results.get_loose_reference("python", "nonexistent")
        self.assertIsNone(retrieved)

    def test_source_files_tracking(self):
        files = ["src/main.py", "src/utils.py", "tests/test_main.py"]
        self.results.add_source_files("python", files)
        retrieved = self.results.get_source_files("python")
        self.assertEqual(retrieved, files)
        empty_files = self.results.get_source_files("typescript")
        self.assertEqual(empty_files, [])

    def test_all_source_files_across_languages(self):
        self.results.add_source_files("python", ["main.py", "utils.py"])
        self.results.add_source_files("typescript", ["index.ts", "app.ts"])
        all_files = self.results.get_all_source_files()
        self.assertEqual(len(all_files), 4)
        self.assertIn("main.py", all_files)
        self.assertIn("index.ts", all_files)

    def test_language_isolation_on_merge(self):
        self.results.add_class_hierarchy("python", {"PythonClass": {}})
        self.results.add_class_hierarchy("typescript", {"TypeScriptClass": {}})
        python_hierarchy = self.results.get_hierarchy("python")
        ts_hierarchy = self.results.get_hierarchy("typescript")
        self.assertIn("PythonClass", python_hierarchy)
        self.assertNotIn("PythonClass", ts_hierarchy)
        self.assertIn("TypeScriptClass", ts_hierarchy)
        self.assertNotIn("TypeScriptClass", python_hierarchy)

    def test_hierarchy_merge_dict_format(self):
        hierarchy1 = {"ClassA": {"superclasses": [], "file_path": "project1/a.py"}}
        hierarchy2 = {"ClassB": {"superclasses": ["ClassA"], "file_path": "project2/b.py"}}
        self.results.add_class_hierarchy("python", hierarchy1)
        self.results.add_class_hierarchy("python", hierarchy2)
        retrieved = self.results.get_hierarchy("python")
        self.assertEqual(len(retrieved), 2)
        self.assertIn("ClassA", retrieved)
        self.assertIn("ClassB", retrieved)

    def test_hierarchy_merge_list_format(self):
        hierarchy1: list[dict] = [{"ClassA": {"superclasses": []}}]
        hierarchy2: list[dict] = [{"ClassB": {"superclasses": ["ClassA"]}}]
        self.results.add_class_hierarchy("python", hierarchy1)
        self.results.add_class_hierarchy("python", hierarchy2)
        retrieved = self.results.get_hierarchy("python")
        self.assertEqual(len(retrieved), 2)
        self.assertIn("ClassA", retrieved)
        self.assertIn("ClassB", retrieved)

    def test_cfg_merge_multiple_projects(self):
        cfg1 = CallGraph()
        node1 = Node("project1.func1", 10, "project1/file.py", 1, 5)
        node2 = Node("project1.func2", 10, "project1/file.py", 6, 10)
        cfg1.add_node(node1)
        cfg1.add_node(node2)
        cfg1.add_edge("project1.func1", "project1.func2")

        cfg2 = CallGraph()
        node3 = Node("project2.func3", 10, "project2/file.py", 1, 5)
        node4 = Node("project2.func4", 10, "project2/file.py", 6, 10)
        cfg2.add_node(node3)
        cfg2.add_node(node4)
        cfg2.add_edge("project2.func3", "project2.func4")

        self.results.add_cfg("python", cfg1)
        self.results.add_cfg("python", cfg2)

        retrieved = self.results.get_cfg("python")
        self.assertEqual(len(retrieved.nodes), 4)
        self.assertEqual(len(retrieved.edges), 2)

    def test_cfg_merge_duplicate_edges(self):
        cfg1 = CallGraph()
        node1 = Node("func1", 10, "file.py", 1, 5)
        node2 = Node("func2", 10, "file.py", 6, 10)
        cfg1.add_node(node1)
        cfg1.add_node(node2)
        cfg1.add_edge("func1", "func2")

        cfg2 = CallGraph()
        cfg2.add_node(node1)
        cfg2.add_node(node2)
        cfg2.add_edge("func1", "func2")

        self.results.add_cfg("python", cfg1)
        self.results.add_cfg("python", cfg2)

        retrieved = self.results.get_cfg("python")
        self.assertEqual(len(retrieved.edges), 1)

    def test_dependencies_merge(self):
        deps1 = {"pkg1": {"imports": ["os"], "imported_by": ["main"]}}
        deps2 = {"pkg2": {"imports": ["sys"], "imported_by": ["utils"]}}
        self.results.add_package_dependencies("python", deps1)
        self.results.add_package_dependencies("python", deps2)
        retrieved = self.results.get_package_dependencies("python")
        self.assertEqual(len(retrieved), 2)
        self.assertIn("pkg1", retrieved)
        self.assertIn("pkg2", retrieved)

    def test_references_merge_multiple_projects(self):
        node1 = Node("project1.Class.method", 6, "project1/file.py", 1, 5)
        node2 = Node("project2.OtherClass.method", 6, "project2/file.py", 1, 5)
        self.results.add_references("python", [node1])
        self.results.add_references("python", [node2])
        retrieved1 = self.results.get_reference("python", "project1.class.method")
        self.assertEqual(retrieved1.fully_qualified_name, "project1.Class.method")
        retrieved2 = self.results.get_reference("python", "project2.otherclass.method")
        self.assertEqual(retrieved2.fully_qualified_name, "project2.OtherClass.method")

    def test_source_files_merge(self):
        files1 = ["project1/main.py", "project1/utils.py"]
        files2 = ["project2/app.py", "project2/helpers.py"]
        self.results.add_source_files("python", files1)
        self.results.add_source_files("python", files2)
        retrieved = self.results.get_source_files("python")
        self.assertEqual(len(retrieved), 4)
        self.assertIn("project1/main.py", retrieved)
        self.assertIn("project2/app.py", retrieved)
