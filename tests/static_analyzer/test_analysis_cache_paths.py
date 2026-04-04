"""Tests that AnalysisCacheManager persists only repo-relative paths to disk.

Regression guard: absolute paths from developer machines were previously
committed inside incremental cache JSON files, causing CI failures when the
wrapper tried to resolve them on a different machine.
"""

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from static_analyzer.analysis_cache import AnalysisCacheManager
from static_analyzer.graph import CallGraph, ClusterResult
from static_analyzer.lsp_client.diagnostics import DiagnosticPosition, DiagnosticRange, LSPDiagnostic
from static_analyzer.node import Node

# Matches any absolute path on Unix or Windows (e.g. /home/..., C:\..., D:/...)
ABSOLUTE_PATH_RE = re.compile(r"(?:[A-Za-z]:[/\\]|^/)")


def _is_absolute(path: str) -> bool:
    return bool(ABSOLUTE_PATH_RE.match(path))


def _build_analysis_result(repo_root: Path) -> dict:
    """Build a minimal but complete analysis result with absolute file paths."""
    abs_main = str(repo_root / "src/main.py")
    abs_util = str(repo_root / "src/utils.py")

    node_a = Node(
        fully_qualified_name="src.main.func_a",
        node_type=12,
        file_path=abs_main,
        line_start=1,
        line_end=10,
    )
    node_b = Node(
        fully_qualified_name="src.utils.func_b",
        node_type=12,
        file_path=abs_util,
        line_start=5,
        line_end=15,
    )

    call_graph = CallGraph()
    call_graph.add_node(node_a)
    call_graph.add_node(node_b)
    call_graph.add_edge("src.main.func_a", "src.utils.func_b")

    ref = Node(
        fully_qualified_name="src.main.MY_CONST",
        node_type=14,
        file_path=abs_main,
        line_start=20,
        line_end=20,
    )

    diag = LSPDiagnostic(
        code="unused-import",
        message="unused",
        severity=2,
        range=DiagnosticRange(
            start=DiagnosticPosition(line=1, character=0),
            end=DiagnosticPosition(line=1, character=10),
        ),
    )

    return {
        "call_graph": call_graph,
        "class_hierarchies": {
            "src.main.MyClass": {
                "superclasses": ["object"],
                "file_path": abs_main,
            }
        },
        "package_relations": {
            "src": {
                "files": [abs_main, abs_util],
            }
        },
        "references": [ref],
        "source_files": [Path(abs_main), Path(abs_util)],
        "diagnostics": {abs_main: [diag]},
    }


class TestCachePathsAreRelative(unittest.TestCase):
    """Verify that every file_path written to the cache JSON is repo-relative."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_root = Path(self.temp_dir)
        self.cache_path = self.repo_root / ".codeboarding" / "cache" / "incremental_cache_python.json"
        self.manager = AnalysisCacheManager(self.repo_root)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _save_and_read_json(self, with_clusters: bool = False) -> dict:
        analysis_result = _build_analysis_result(self.repo_root)
        if with_clusters:
            cluster_results = {
                "python": ClusterResult(
                    clusters={0: {"src.main.func_a", "src.utils.func_b"}},
                    file_to_clusters={
                        str(self.repo_root / "src/main.py"): {0},
                        str(self.repo_root / "src/utils.py"): {0},
                    },
                    cluster_to_files={
                        0: {str(self.repo_root / "src/main.py"), str(self.repo_root / "src/utils.py")},
                    },
                    strategy="louvain",
                ),
            }
            self.manager.save_cache_with_clusters(self.cache_path, analysis_result, cluster_results, "abc123", 1)
        else:
            self.manager.save_cache(self.cache_path, analysis_result, "abc123", 1)

        with open(self.cache_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _collect_file_paths(self, data: dict) -> list[str]:
        """Recursively collect all values associated with 'file_path' or 'files' keys."""
        paths: list[str] = []

        if isinstance(data, dict):
            for key, value in data.items():
                if key == "file_path" and isinstance(value, str):
                    paths.append(value)
                elif key == "files" and isinstance(value, list):
                    paths.extend(v for v in value if isinstance(v, str))
                elif key == "source_files" and isinstance(value, list):
                    paths.extend(v for v in value if isinstance(v, str))
                elif key == "file_to_clusters" and isinstance(value, dict):
                    paths.extend(value.keys())
                elif key == "cluster_to_files" and isinstance(value, dict):
                    for file_list in value.values():
                        if isinstance(file_list, list):
                            paths.extend(f for f in file_list if isinstance(f, str))
                else:
                    paths.extend(self._collect_file_paths(value))
            # Also collect keys that are file paths in diagnostics (keyed by file_path)
            if "diagnostics" in data and isinstance(data["diagnostics"], dict):
                # diagnostics keys are file paths — already collected via recursion
                pass
        elif isinstance(data, list):
            for item in data:
                paths.extend(self._collect_file_paths(item))

        return paths

    def _collect_diagnostics_keys(self, data: dict) -> list[str]:
        """Collect the top-level keys of the diagnostics dict (they are file paths)."""
        diag = data.get("diagnostics", {})
        return list(diag.keys()) if isinstance(diag, dict) else []

    def test_save_cache_writes_relative_paths(self):
        """All file paths in the saved cache JSON must be relative."""
        cache_data = self._save_and_read_json(with_clusters=False)
        all_paths = self._collect_file_paths(cache_data) + self._collect_diagnostics_keys(cache_data)

        self.assertTrue(len(all_paths) > 0, "Expected to find file paths in cache JSON")
        for path in all_paths:
            self.assertFalse(
                _is_absolute(path),
                f"Absolute path found in cache JSON: {path}",
            )

    def test_save_cache_with_clusters_writes_relative_paths(self):
        """All file paths (including cluster data) must be relative."""
        cache_data = self._save_and_read_json(with_clusters=True)
        all_paths = self._collect_file_paths(cache_data) + self._collect_diagnostics_keys(cache_data)

        self.assertTrue(len(all_paths) > 0, "Expected to find file paths in cache JSON")
        for path in all_paths:
            self.assertFalse(
                _is_absolute(path),
                f"Absolute path found in cache JSON: {path}",
            )

    def test_no_absolute_paths_anywhere_in_json(self):
        """Brute-force check: scan all string values in the JSON for absolute paths."""
        cache_data = self._save_and_read_json(with_clusters=True)

        def scan_strings(obj: object, breadcrumb: str = "") -> list[str]:
            violations: list[str] = []
            if isinstance(obj, str) and _is_absolute(obj):
                violations.append(f"{breadcrumb} = {obj}")
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    # Check keys too (diagnostics uses file paths as keys)
                    if isinstance(k, str) and _is_absolute(k):
                        violations.append(f"{breadcrumb}[key] = {k}")
                    violations.extend(scan_strings(v, f"{breadcrumb}.{k}"))
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    violations.extend(scan_strings(v, f"{breadcrumb}[{i}]"))
            return violations

        violations = scan_strings(cache_data)
        self.assertEqual(violations, [], f"Found absolute paths in cache JSON:\n" + "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
