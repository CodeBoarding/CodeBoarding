import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from static_analyzer.analysis_cache import AnalysisCacheManager
from static_analyzer.graph import CallGraph, Node


def _build_analysis_result() -> dict:
    call_graph = CallGraph()
    node = Node(
        fully_qualified_name="src.module.func",
        node_type=Node.FUNCTION_TYPE,
        file_path="src/module.py",
        line_start=1,
        line_end=10,
    )
    call_graph.add_node(node)

    return {
        "call_graph": call_graph,
        "class_hierarchies": {},
        "package_relations": {},
        "references": [node],
        "source_files": [Path("src/module.py")],
    }


class TestAnalysisCacheManagerVersioning(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cache_path = Path(self.temp_dir) / "incremental_cache_python.json"
        self.manager = AnalysisCacheManager()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_and_load_roundtrip(self):
        analysis_result = _build_analysis_result()
        self.manager.save_cache(self.cache_path, analysis_result, commit_hash="abc123", iteration_id=1)

        loaded = self.manager.load_cache(self.cache_path)
        self.assertIsNotNone(loaded)
        if loaded is None:
            return

        loaded_analysis, commit_hash, iteration_id = loaded
        self.assertEqual(commit_hash, "abc123")
        self.assertEqual(iteration_id, 1)
        self.assertIn("call_graph", loaded_analysis)

    def test_schema_version_mismatch_invalidates_cache(self):
        analysis_result = _build_analysis_result()
        self.manager.save_cache(self.cache_path, analysis_result, commit_hash="abc123", iteration_id=1)

        data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        data["metadata"]["schema_version"] = 999
        self.cache_path.write_text(json.dumps(data), encoding="utf-8")

        loaded = self.manager.load_cache(self.cache_path)
        self.assertIsNone(loaded)

    @patch("static_analyzer.analysis_cache.get_codeboarding_version")
    def test_codeboarding_version_mismatch_invalidates_cache(self, mock_get_version):
        mock_get_version.return_value = "0.2.0"
        analysis_result = _build_analysis_result()
        self.manager.save_cache(self.cache_path, analysis_result, commit_hash="abc123", iteration_id=1)

        mock_get_version.return_value = "9.9.9"
        loaded = self.manager.load_cache(self.cache_path)
        self.assertIsNone(loaded)


if __name__ == "__main__":
    unittest.main()
