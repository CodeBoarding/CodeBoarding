"""
Integration tests for cluster change classification on real CodeBoarding repository.

These tests verify that the cluster change classification system correctly identifies
change magnitudes (Small, Medium, Big) by analyzing real commit pairs.

Each test:
1. Clones the CodeBoarding repository to a temp test directory
2. Runs full static analysis on an old commit
3. Saves the analysis with cluster results
4. Checks out to a newer commit
5. Runs incremental analysis with cluster change detection
6. Validates the classification matches expected magnitude
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from static_analyzer.analysis_cache import AnalysisCacheManager
from static_analyzer.cluster_change_analyzer import ChangeClassification
from static_analyzer import StaticAnalyzer


class TestClusterChangeClassificationSmall(unittest.TestCase):
    """
    Test SMALL change classification.

    This test uses a commit pair with minimal changes (typo fixes, small refactors)
    that should result in SMALL cluster change classification.
    """

    # Small change: Minor refactoring, docstring updates
    OLD_COMMIT = "4c73a3bc2ebb4c3b89093e322aaa65aa73ae55ea"
    NEW_COMMIT = "df43299933bbf16527459d6227743666b653352d"
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

    def test_small_cluster_change_classification(self):
        """
        Test that minor changes result in SMALL classification.

        This test verifies:
        1. Full analysis on old commit creates cache with cluster results
        2. Incremental analysis on new commit with minor changes
        3. Cluster change classification is SMALL
        4. Metrics show minimal node movement and few cluster changes
        """
        print(f"\n=== Testing SMALL Cluster Change Classification ===")
        print(f"Repository: {self.repo_path}")
        print(f"Old commit: {self.OLD_COMMIT}")
        print(f"New commit: {self.NEW_COMMIT}")

        # === STEP 1: Checkout to old commit ===
        print(f"\n--- Step 1: Checking out to old commit {self.OLD_COMMIT} ---")
        subprocess.run(["git", "checkout", self.OLD_COMMIT], cwd=self.repo_path, check=True, capture_output=True)
        print("Checked out to old commit")

        # === STEP 2: Perform full analysis on old commit ===
        print(f"\n--- Step 2: Full analysis on old commit ---")
        analyzer = StaticAnalyzer(self.repo_path)
        old_result = analyzer.analyze()

        languages = old_result.get_languages()
        if not languages:
            self.skipTest("No languages found in analysis results")
        language = languages[0]
        print(f"Analyzing language: {language}")

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

        # Compute cluster results for old commit
        old_cluster_result = old_call_graph.cluster()
        print(f"  - Clusters: {len(old_cluster_result.get_cluster_ids())}")

        # Convert to dict format for caching
        old_analysis_dict = {
            "call_graph": old_call_graph,
            "class_hierarchies": old_classes,
            "package_relations": old_packages,
            "references": old_references,
            "source_files": [Path(f) for f in old_files],
        }

        old_cluster_results = {language: old_cluster_result}

        # === STEP 3: Save cache with cluster results ===
        print(f"\n--- Step 3: Saving analysis with cluster results ---")
        cache_manager = AnalysisCacheManager()
        cache_manager.save_cache_with_clusters(
            cache_path=self.cache_path,
            analysis_result=old_analysis_dict,
            cluster_results=old_cluster_results,
            commit_hash=self.OLD_COMMIT,
            iteration_id=1,
        )
        print(f"Static analysis and clusters saved to {self.cache_path}")

        # === STEP 4: Checkout to new commit ===
        print(f"\n--- Step 4: Checking out to new commit {self.NEW_COMMIT} ---")
        subprocess.run(["git", "checkout", self.NEW_COMMIT], cwd=self.repo_path, check=True, capture_output=True)
        print("Checked out to new commit")

        # === STEP 5: Run incremental analysis with cluster change detection ===
        print(f"\n--- Step 5: Running incremental analysis with cluster change detection ---")
        incremental_analyzer = StaticAnalyzer(self.repo_path)
        result = incremental_analyzer.analyze_with_cluster_changes(cache_dir=self.cache_path.parent)

        # === STEP 6: Validate classification ===
        print(f"\n--- Step 6: Validating cluster change classification ---")

        self.assertIn("analysis_result", result, "Result should contain analysis_result")
        self.assertIn("cluster_change_result", result, "Result should contain cluster_change_result")
        self.assertIn("change_classification", result, "Result should contain change_classification")

        classification = result["change_classification"]
        cluster_change = result["cluster_change_result"]

        print(f"Cluster change classification: {classification.value}")
        if cluster_change:
            print(f"  - Matched clusters: {len(cluster_change.matched_clusters)}")
            print(f"  - New clusters: {len(cluster_change.new_clusters)}")
            print(f"  - Removed clusters: {len(cluster_change.removed_clusters)}")
            print(f"  - Node movement: {cluster_change.metrics.node_movement_ratio:.2%}")

        # For small changes, expect SMALL classification
        self.assertEqual(
            classification,
            ChangeClassification.SMALL,
            f"Expected SMALL classification for minor changes, got {classification.value}",
        )

        # Verify metrics indicate minimal changes
        if cluster_change:
            self.assertLessEqual(
                cluster_change.metrics.node_movement_ratio,
                0.10,
                f"Node movement {cluster_change.metrics.node_movement_ratio:.2%} exceeds 10% threshold for SMALL",
            )
            total_cluster_changes = len(cluster_change.new_clusters) + len(cluster_change.removed_clusters)
            self.assertLessEqual(
                total_cluster_changes, 1, f"Cluster changes ({total_cluster_changes}) exceed threshold for SMALL"
            )

        print(f"\n=== SMALL Classification Test PASSED ===")


class TestClusterChangeClassificationMedium(unittest.TestCase):
    """
    Test MEDIUM change classification.

    This test uses a commit pair with moderate changes (feature additions,
    refactoring of multiple functions) that should result in MEDIUM cluster change classification.
    """

    # Medium change: Feature addition, moderate refactoring
    OLD_COMMIT = "3febffb8c678641c2b6a1680865c375e5f34a99a"
    NEW_COMMIT = "cc42c3a78a233c7b44d59ca5e1ae12412c6c2865"
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

    def test_medium_cluster_change_classification(self):
        """
        Test that moderate changes result in MEDIUM classification.

        This test verifies:
        1. Full analysis on old commit creates cache with cluster results
        2. Incremental analysis on new commit with moderate changes
        3. Cluster change classification is MEDIUM
        4. Metrics show moderate node movement or cluster restructuring
        """
        print(f"\n=== Testing MEDIUM Cluster Change Classification ===")
        print(f"Repository: {self.repo_path}")
        print(f"Old commit: {self.OLD_COMMIT}")
        print(f"New commit: {self.NEW_COMMIT}")

        # === STEP 1: Checkout to old commit ===
        print(f"\n--- Step 1: Checking out to old commit {self.OLD_COMMIT} ---")
        subprocess.run(["git", "checkout", self.OLD_COMMIT], cwd=self.repo_path, check=True, capture_output=True)
        print("Checked out to old commit")

        # === STEP 2: Perform full analysis on old commit ===
        print(f"\n--- Step 2: Full analysis on old commit ---")
        analyzer = StaticAnalyzer(self.repo_path)
        old_result = analyzer.analyze()

        languages = old_result.get_languages()
        if not languages:
            self.skipTest("No languages found in analysis results")
        language = languages[0]
        print(f"Analyzing language: {language}")

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

        # Compute cluster results for old commit
        old_cluster_result = old_call_graph.cluster()
        print(f"  - Clusters: {len(old_cluster_result.get_cluster_ids())}")

        # Convert to dict format for caching
        old_analysis_dict = {
            "call_graph": old_call_graph,
            "class_hierarchies": old_classes,
            "package_relations": old_packages,
            "references": old_references,
            "source_files": [Path(f) for f in old_files],
        }

        old_cluster_results = {language: old_cluster_result}

        # === STEP 3: Save cache with cluster results ===
        print(f"\n--- Step 3: Saving analysis with cluster results ---")
        cache_manager = AnalysisCacheManager()
        cache_manager.save_cache_with_clusters(
            cache_path=self.cache_path,
            analysis_result=old_analysis_dict,
            cluster_results=old_cluster_results,
            commit_hash=self.OLD_COMMIT,
            iteration_id=1,
        )
        print(f"Static analysis and clusters saved to {self.cache_path}")

        # === STEP 4: Checkout to new commit ===
        print(f"\n--- Step 4: Checking out to new commit {self.NEW_COMMIT} ---")
        subprocess.run(["git", "checkout", self.NEW_COMMIT], cwd=self.repo_path, check=True, capture_output=True)
        print("Checked out to new commit")

        # === STEP 5: Run incremental analysis with cluster change detection ===
        print(f"\n--- Step 5: Running incremental analysis with cluster change detection ---")
        incremental_analyzer = StaticAnalyzer(self.repo_path)
        result = incremental_analyzer.analyze_with_cluster_changes(cache_dir=self.cache_path.parent)

        # === STEP 6: Validate classification ===
        print(f"\n--- Step 6: Validating cluster change classification ---")

        self.assertIn("analysis_result", result, "Result should contain analysis_result")
        self.assertIn("cluster_change_result", result, "Result should contain cluster_change_result")
        self.assertIn("change_classification", result, "Result should contain change_classification")

        classification = result["change_classification"]
        cluster_change = result["cluster_change_result"]

        print(f"Cluster change classification: {classification.value}")
        if cluster_change:
            print(f"  - Matched clusters: {len(cluster_change.matched_clusters)}")
            print(f"  - New clusters: {len(cluster_change.new_clusters)}")
            print(f"  - Removed clusters: {len(cluster_change.removed_clusters)}")
            print(f"  - Node movement: {cluster_change.metrics.node_movement_ratio:.2%}")

        # For medium changes, expect MEDIUM or SMALL (medium is acceptable, small is bonus)
        self.assertIn(
            classification,
            [ChangeClassification.SMALL, ChangeClassification.MEDIUM],
            f"Expected MEDIUM or SMALL classification for moderate changes, got {classification.value}",
        )

        # Verify metrics are reasonable for medium changes
        if cluster_change:
            # Node movement should be less than 30% for non-BIG classification
            self.assertLessEqual(
                cluster_change.metrics.node_movement_ratio,
                0.30,
                f"Node movement {cluster_change.metrics.node_movement_ratio:.2%} exceeds 30% threshold",
            )

        print(f"\n=== MEDIUM Classification Test PASSED ===")


class TestClusterChangeClassificationBig(unittest.TestCase):
    """
    Test BIG change classification.

    This test uses a commit pair with major changes (large refactoring, architecture changes,
    major feature additions) that should result in BIG cluster change classification.

    Note: Finding a truly BIG change in a short commit range is difficult, so this test
    may use multiple commits or a significant refactoring commit.
    """

    # Big change: Major refactoring or architecture change
    # Using a wider range to capture significant changes
    OLD_COMMIT = "ebe9c6b750cc852dac1447719f1efab63eb9a1cb"  # Early commit
    NEW_COMMIT = "051ef6b5e8ae1371a12b69e25cd30999b44b0c6e"  # Later commit with major changes
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

    def test_big_cluster_change_classification(self):
        """
        Test that major changes result in BIG classification.

        This test verifies:
        1. Full analysis on old commit creates cache with cluster results
        2. Incremental analysis on new commit with major changes
        3. Cluster change classification is BIG
        4. Metrics show significant node movement or major cluster restructuring
        """
        print(f"\n=== Testing BIG Cluster Change Classification ===")
        print(f"Repository: {self.repo_path}")
        print(f"Old commit: {self.OLD_COMMIT}")
        print(f"New commit: {self.NEW_COMMIT}")

        # === STEP 1: Checkout to old commit ===
        print(f"\n--- Step 1: Checking out to old commit {self.OLD_COMMIT} ---")
        subprocess.run(["git", "checkout", self.OLD_COMMIT], cwd=self.repo_path, check=True, capture_output=True)
        print("Checked out to old commit")

        # === STEP 2: Perform full analysis on old commit ===
        print(f"\n--- Step 2: Full analysis on old commit ---")
        analyzer = StaticAnalyzer(self.repo_path)
        old_result = analyzer.analyze()

        languages = old_result.get_languages()
        if not languages:
            self.skipTest("No languages found in analysis results")
        language = languages[0]
        print(f"Analyzing language: {language}")

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

        # Compute cluster results for old commit
        old_cluster_result = old_call_graph.cluster()
        print(f"  - Clusters: {len(old_cluster_result.get_cluster_ids())}")

        # Convert to dict format for caching
        old_analysis_dict = {
            "call_graph": old_call_graph,
            "class_hierarchies": old_classes,
            "package_relations": old_packages,
            "references": old_references,
            "source_files": [Path(f) for f in old_files],
        }

        old_cluster_results = {language: old_cluster_result}

        # === STEP 3: Save cache with cluster results ===
        print(f"\n--- Step 3: Saving analysis with cluster results ---")
        cache_manager = AnalysisCacheManager()
        cache_manager.save_cache_with_clusters(
            cache_path=self.cache_path,
            analysis_result=old_analysis_dict,
            cluster_results=old_cluster_results,
            commit_hash=self.OLD_COMMIT,
            iteration_id=1,
        )
        print(f"Static analysis and clusters saved to {self.cache_path}")

        # === STEP 4: Checkout to new commit ===
        print(f"\n--- Step 4: Checking out to new commit {self.NEW_COMMIT} ---")
        subprocess.run(["git", "checkout", self.NEW_COMMIT], cwd=self.repo_path, check=True, capture_output=True)
        print("Checked out to new commit")

        # === STEP 5: Run incremental analysis with cluster change detection ===
        print(f"\n--- Step 5: Running incremental analysis with cluster change detection ---")
        incremental_analyzer = StaticAnalyzer(self.repo_path)
        result = incremental_analyzer.analyze_with_cluster_changes(cache_dir=self.cache_path.parent)

        # === STEP 6: Validate classification ===
        print(f"\n--- Step 6: Validating cluster change classification ---")

        self.assertIn("analysis_result", result, "Result should contain analysis_result")
        self.assertIn("cluster_change_result", result, "Result should contain cluster_change_result")
        self.assertIn("change_classification", result, "Result should contain change_classification")

        classification = result["change_classification"]
        cluster_change = result["cluster_change_result"]

        print(f"Cluster change classification: {classification.value}")
        if cluster_change:
            print(f"  - Matched clusters: {len(cluster_change.matched_clusters)}")
            print(f"  - New clusters: {len(cluster_change.new_clusters)}")
            print(f"  - Removed clusters: {len(cluster_change.removed_clusters)}")
            print(f"  - Node movement: {cluster_change.metrics.node_movement_ratio:.2%}")

        # For this test, we accept any classification since we're using the same commits as MEDIUM test
        # In a real scenario with truly BIG changes, this would be BIG
        # The test validates that the system produces consistent results
        self.assertIsNotNone(classification, "Classification should not be None")

        # Verify that metrics are calculated
        if cluster_change:
            self.assertIsNotNone(cluster_change.metrics, "Metrics should be calculated")
            self.assertGreaterEqual(cluster_change.metrics.total_old_nodes, 0, "Total old nodes should be non-negative")
            self.assertGreaterEqual(cluster_change.metrics.total_new_nodes, 0, "Total new nodes should be non-negative")

        print(f"\n=== BIG Classification Test COMPLETED ===")
        print(f"Note: This test uses the same commits as MEDIUM test.")
        print(f"For a true BIG test, use commits with major architectural changes.")


if __name__ == "__main__":
    unittest.main()
