"""
Integration test for two-commit incremental analysis workflow on real CodeBoarding repository.

This test:
1. Clones the CodeBoarding repository to a temp test directory
2. Runs static analysis on an old commit and validates number of edges, nodes, and clusters
3. Saves the static analysis
4. Checks out to a newer commit
5. Loads the saved static analysis
6. Runs incremental static analysis on that
7. Validates number of nodes, edges, and clusters
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from static_analyzer.analysis_cache import AnalysisCacheManager
from static_analyzer import StaticAnalyzer


class TestRealRepoTwoCommitIntegration(unittest.TestCase):
    """Integration test for two-commit incremental analysis workflow on real repository."""

    OLD_COMMIT = "e03132c97997a6dabf68cd5d2df6432601360edb"
    NEW_COMMIT = "03b25afe8d37ce733e5f70c3cbcdfb52f4883dcd"
    REPO_URL = "https://github.com/CodeBoarding/CodeBoarding.git"

    def setUp(self):
        """Set up test environment by cloning the repository."""
        self.temp_dir = tempfile.mkdtemp()
        self.repo_path = Path(self.temp_dir) / "CodeBoarding"
        self.cache_path = self.repo_path / "incremental_cache_Python.json"

        # Clone the repository
        print(f"\n=== Cloning repository from {self.REPO_URL} ===")
        subprocess.run(["git", "clone", self.REPO_URL, str(self.repo_path)], capture_output=True, text=True, check=True)
        print(f"Repository cloned to {self.repo_path}")

    def tearDown(self):
        """Clean up test environment."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_two_commit_incremental_analysis_workflow(self):
        """
        Test the complete two-commit incremental analysis workflow on real repository.

        This test verifies:
        1. Full analysis on old commit creates cache
        2. Incremental analysis on new commit reuses cache and updates only changed files
        3. Final results contain correct number of nodes and edges
        """
        print(f"\n=== Testing Two-Commit Incremental Analysis Workflow ===")
        print(f"Repository: {self.repo_path}")
        print(f"Cache path: {self.cache_path}")
        print(f"Old commit: {self.OLD_COMMIT}")
        print(f"New commit: {self.NEW_COMMIT}")

        # === STEP 1: Checkout to old commit ===
        print(f"\n--- Step 1: Checking out to old commit {self.OLD_COMMIT} ---")
        subprocess.run(["git", "checkout", self.OLD_COMMIT], cwd=self.repo_path, check=True, capture_output=True)
        print(f"Checked out to old commit")

        # === STEP 2: Perform full analysis on old commit ===
        print(f"\n--- Step 2: Full analysis on old commit ---")

        # Create static analyzer
        analyzer = StaticAnalyzer(self.repo_path)

        # Perform full analysis
        print("Starting full static analysis...")
        old_result = analyzer.analyze()

        # Get the language (should be Python for CodeBoarding)
        languages = old_result.get_languages()
        if not languages:
            self.skipTest("No languages found in analysis results")
        language = languages[0]
        print(f"Analyzing language: {language}")

        # Extract results using getter methods
        old_call_graph = old_result.get_cfg(language)
        old_references = list(old_result.results[language].get("references", {}).values())
        old_classes = old_result.get_hierarchy(language)
        old_packages = old_result.get_package_dependencies(language)
        old_files = old_result.get_source_files(language)

        print(f"Old commit analysis results:")
        print(f"  - Files: {len(old_files)}")
        print(f"  - References: {len(old_references)}")
        print(f"  - Classes: {len(old_classes)}")
        print(f"  - Packages: {len(old_packages)}")
        print(f"  - Call graph nodes: {len(old_call_graph.nodes)}")
        print(f"  - Call graph edges: {len(old_call_graph.edges)}")

        # === STEP 3: Validate old commit results (placeholders) ===
        print(f"\n--- Step 3: Validating old commit results ---")

        expected_old_references = 2668
        expected_old_classes = 114
        expected_old_packages = 13
        expected_old_nodes = 2672
        expected_old_edges = 1012

        # expected_old_clusters = 0  # PLACEHOLDER

        # Convert StaticAnalysisResults to dict format for caching
        # Note: old_references is already a list of Node objects
        old_analysis_dict = {
            "call_graph": old_call_graph,
            "class_hierarchies": old_classes,
            "package_relations": old_packages,
            "references": old_references,
            "source_files": [Path(f) for f in old_files],
        }

        self.assertEqual(len(old_references), expected_old_references, "Old commit reference count mismatch")
        self.assertEqual(len(old_call_graph.nodes), expected_old_nodes, "Old commit node count mismatch")
        # I want to validate that expected_old_edges is close to actual edges within 5% margin
        tolerance = expected_old_edges * 0.05
        self.assertTrue(
            abs(len(old_call_graph.edges) - expected_old_edges) <= tolerance,
            f"Old commit edge count mismatch: expected ~{expected_old_edges} (±5%), got {len(old_call_graph.edges)}",
        )
        self.assertEqual(len(old_packages), expected_old_packages, "Old commit packages count mismatch")
        self.assertEqual(len(old_classes), expected_old_classes, "Old commit class count mismatch")

        cache_manager = AnalysisCacheManager()
        cache_manager.save_cache(
            cache_path=self.cache_path, analysis_result=old_analysis_dict, commit_hash=self.OLD_COMMIT, iteration_id=1
        )
        print(f"Static analysis saved to {self.cache_path}")

        # === STEP 5: Checkout to new commit ===
        print(f"\n--- Step 5: Checking out to new commit {self.NEW_COMMIT} ---")
        subprocess.run(["git", "checkout", self.NEW_COMMIT], cwd=self.repo_path, check=True, capture_output=True)
        print(f"Checked out to new commit")

        # === STEP 6: Load saved static analysis ===
        print(f"\n--- Step 6: Loading saved static analysis ---")

        cache_result = cache_manager.load_cache(self.cache_path)
        self.assertIsNotNone(cache_result, "Failed to load cache")

        loaded_analysis, loaded_commit, loaded_iteration = cache_result
        self.assertEqual(loaded_commit, self.OLD_COMMIT, "Loaded commit mismatch")
        self.assertEqual(loaded_iteration, 1, "Loaded iteration mismatch")

        print(f"Loaded static analysis from cache:")
        print(f"  - Commit: {loaded_commit}")
        print(f"  - Iteration: {loaded_iteration}")
        print(f"  - Nodes: {len(loaded_analysis['call_graph'].nodes)}")
        print(f"  - Edges: {len(loaded_analysis['call_graph'].edges)}")

        # === STEP 7: Run incremental static analysis using StaticAnalyzer with cache ===
        print(f"\n--- Step 7: Running incremental static analysis via StaticAnalyzer ---")

        # Use StaticAnalyzer with cache_dir to trigger incremental analysis
        # The cache_dir should be the parent of cache_path since StaticAnalyzer generates cache filenames
        incremental_analyzer = StaticAnalyzer(self.repo_path)
        new_result = incremental_analyzer.analyze(cache_dir=self.cache_path.parent)

        # Get the language (should be Python for CodeBoarding)
        new_languages = new_result.get_languages()
        if not new_languages:
            self.skipTest("No languages found in incremental analysis results")
        new_language = new_languages[0]

        # Extract results using getter methods (same as full analysis)
        new_call_graph = new_result.get_cfg(new_language)
        new_references = list(new_result.results[new_language].get("references", {}).values())
        new_classes = new_result.get_hierarchy(new_language)
        new_packages = new_result.get_package_dependencies(new_language)
        new_files = new_result.get_source_files(new_language)

        print(f"New commit incremental analysis results:")
        print(f"  - Files: {len(new_files)}")
        print(f"  - References: {len(new_references)}")
        print(f"  - Classes: {len(new_classes)}")
        print(f"  - Packages: {len(new_packages)}")
        print(f"  - Call graph nodes: {len(new_call_graph.nodes)}")
        print(f"  - Call graph edges: {len(new_call_graph.edges)}")

        # === STEP 8: Validate incremental analysis results ===
        print(f"\n--- Step 8: Validating new commit incremental analysis results ---")

        expected_new_references = 2559
        expected_new_classes = 111
        expected_new_packages = 13
        expected_new_nodes = 2563
        expected_new_edges = 883

        self.assertEqual(len(new_classes), expected_new_classes, "New commit class count mismatch")
        self.assertEqual(len(new_packages), expected_new_packages, "New commit package count mismatch")
        self.assertEqual(len(new_call_graph.nodes), expected_new_nodes, "New commit node count mismatch")
        self.assertEqual(len(new_references), expected_new_references, "New commit reference count mismatch")

        edge_tolerance = expected_new_edges * 0.05
        self.assertTrue(
            abs(len(new_call_graph.edges) - expected_new_edges) <= edge_tolerance,
            f"New commit edge count mismatch: expected ~{expected_new_edges} (±5%), got {len(new_call_graph.edges)}",
        )

        print(f"Incremental analysis validation successful!")

        # === STEP 9: Verify incremental analysis updated the cache ===
        print(f"\n--- Step 9: Verifying incremental analysis updated the cache ---")

        # Verify that the cache was updated
        updated_cache_result = cache_manager.load_cache(self.cache_path)
        self.assertIsNotNone(updated_cache_result, "Failed to load updated cache")

        updated_analysis, updated_commit, updated_iteration = updated_cache_result
        self.assertEqual(updated_commit, self.NEW_COMMIT, "Updated cache commit mismatch")
        self.assertEqual(updated_iteration, 2, "Updated cache iteration mismatch")

        print(f"Cache successfully updated:")
        print(f"  - Commit: {updated_commit}")
        print(f"  - Iteration: {updated_iteration}")

        print(f"\n=== Two-Commit Integration Test PASSED ===")


if __name__ == "__main__":
    unittest.main()
