"""Tests for file_manager module."""

import pytest
from pathlib import Path

from diagram_analysis.incremental.file_manager import (
    assign_new_files,
    remove_deleted_files,
    get_new_files_for_component,
)
from diagram_analysis.manifest import AnalysisManifest, build_manifest_from_analysis
from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileMethodGroup,
    SourceCodeReference,
    hash_component_id,
    ROOT_PARENT_ID,
)


COMP_A_ID = hash_component_id(ROOT_PARENT_ID, "ComponentA")
COMP_B_ID = hash_component_id(ROOT_PARENT_ID, "ComponentB")


@pytest.fixture
def sample_analysis() -> AnalysisInsights:
    """Create a sample analysis for testing."""
    return AnalysisInsights(
        description="Test project description",
        components=[
            Component(
                name="ComponentA",
                component_id=COMP_A_ID,
                description="First component",
                key_entities=[
                    SourceCodeReference(
                        qualified_name="module_a.ClassA",
                        reference_file="src/module_a.py",
                        reference_start_line=10,
                        reference_end_line=50,
                    )
                ],
                file_methods=[
                    FileMethodGroup(file_path="src/module_a.py"),
                    FileMethodGroup(file_path="src/module_a_utils.py"),
                ],
                source_cluster_ids=[1, 2],
            ),
            Component(
                name="ComponentB",
                component_id=COMP_B_ID,
                description="Second component",
                key_entities=[
                    SourceCodeReference(
                        qualified_name="module_b.ClassB",
                        reference_file="lib/module_b.py",
                        reference_start_line=None,
                        reference_end_line=None,
                    )
                ],
                file_methods=[FileMethodGroup(file_path="lib/module_b.py")],
                source_cluster_ids=[3],
            ),
        ],
        components_relations=[],
    )


@pytest.fixture
def sample_manifest(sample_analysis: AnalysisInsights) -> AnalysisManifest:
    """Create a sample manifest for testing."""
    return build_manifest_from_analysis(
        analysis=sample_analysis,
        repo_state_hash="abc1234_deadbeef",
        base_commit="abc1234567890",
        expanded_components=[COMP_A_ID],
    )


class TestAssignNewFiles:
    """Tests for assign_new_files function."""

    def test_assign_new_files_to_existing_component(self, sample_analysis, sample_manifest):
        """Test that new files in the same directory are assigned to the correct component."""
        new_files = ["src/new_file.py"]

        result = assign_new_files(new_files, sample_analysis, sample_manifest)

        assert COMP_A_ID in result
        file_paths_a = [fg.file_path for fg in sample_analysis.components[0].file_methods]
        assert "src/new_file.py" in file_paths_a
        assert sample_manifest.file_to_component["src/new_file.py"] == COMP_A_ID

    def test_assign_new_files_skip_test_files(self, sample_analysis, sample_manifest):
        """Test that test files are skipped based on should_skip_file patterns."""
        new_files = ["src/test_module.py", "tests/test_file.py", "src/module_test.py"]

        result = assign_new_files(new_files, sample_analysis, sample_manifest)

        assert len(result) == 0
        assert "src/test_module.py" not in sample_manifest.file_to_component

    def test_assign_new_files_multiple_components(self, sample_analysis, sample_manifest):
        """Test assigning files to multiple components based on directory."""
        new_files = ["src/another_a.py", "lib/another_b.py"]

        result = assign_new_files(new_files, sample_analysis, sample_manifest)

        assert COMP_A_ID in result
        assert COMP_B_ID in result
        file_paths_a = [fg.file_path for fg in sample_analysis.components[0].file_methods]
        file_paths_b = [fg.file_path for fg in sample_analysis.components[1].file_methods]
        assert "src/another_a.py" in file_paths_a
        assert "lib/another_b.py" in file_paths_b

    def test_assign_new_files_no_matching_component(self, sample_analysis, sample_manifest, caplog):
        """Test that files in new directories are not assigned when no matching component exists."""
        import logging

        new_files = ["new_dir/file.py"]

        with caplog.at_level(logging.DEBUG):
            result = assign_new_files(new_files, sample_analysis, sample_manifest)

        assert len(result) == 0
        assert "Could not assign new file" in caplog.text


class TestRemoveDeletedFiles:
    """Tests for remove_deleted_files function."""

    def test_remove_deleted_files_from_component(self, sample_analysis, sample_manifest):
        """Test that deleted files are removed from analysis and manifest."""
        deleted_files = ["src/module_a.py"]

        remove_deleted_files(deleted_files, sample_analysis, sample_manifest)

        assert "src/module_a.py" not in sample_manifest.file_to_component
        file_paths_a = [fg.file_path for fg in sample_analysis.components[0].file_methods]
        assert "src/module_a.py" not in file_paths_a
        # Check that the key entity was also removed
        assert len(sample_analysis.components[0].key_entities) == 0

    def test_remove_multiple_deleted_files(self, sample_analysis, sample_manifest):
        """Test removing multiple deleted files."""
        deleted_files = ["src/module_a.py", "src/module_a_utils.py"]

        remove_deleted_files(deleted_files, sample_analysis, sample_manifest)

        component_a = sample_analysis.components[0]
        assert len(component_a.file_methods) == 0
        assert "src/module_a.py" not in sample_manifest.file_to_component
        assert "src/module_a_utils.py" not in sample_manifest.file_to_component

    def test_remove_nonexistent_file(self, sample_analysis, sample_manifest):
        """Test that removing a file not in the manifest/analysis is handled gracefully."""
        deleted_files = ["nonexistent/file.py"]

        # Should not raise an exception
        remove_deleted_files(deleted_files, sample_analysis, sample_manifest)

        # Verify original state is unchanged
        assert len(sample_analysis.components[0].file_methods) == 2


class TestGetNewFilesForComponent:
    """Tests for get_new_files_for_component function."""

    def test_get_new_files_for_existing_component(self, sample_analysis):
        """Test retrieving new files for an existing component."""
        added_files = ["src/module_a.py", "src/new_file.py", "lib/module_b.py"]

        result = get_new_files_for_component(COMP_A_ID, added_files, sample_analysis)

        assert "src/module_a.py" in result
        assert "src/new_file.py" not in result  # Not in component's file_methods

    def test_get_new_files_for_nonexistent_component(self, sample_analysis):
        """Test that empty list is returned for non-existent component."""
        added_files = ["src/file.py"]

        result = get_new_files_for_component("NonExistent", added_files, sample_analysis)

        assert result == []

    def test_get_new_files_path_matching(self, sample_analysis):
        """Test that path matching works for relative vs absolute paths."""
        # Test with absolute-like path that ends with relative path
        added_files = ["/full/path/src/module_a.py"]

        result = get_new_files_for_component(COMP_A_ID, added_files, sample_analysis)

        assert "/full/path/src/module_a.py" in result
