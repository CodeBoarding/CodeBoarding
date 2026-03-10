"""Tests for scoped_analysis module."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from diagram_analysis.incremental.scoped_analysis import (
    analyze_expanded_component_impacts,
    run_scoped_component_impacts,
)
from diagram_analysis.incremental.models import ChangeImpact, UpdateAction
from diagram_analysis.manifest import AnalysisManifest
from agents.agent_responses import AnalysisInsights, Component
from repo_utils.change_detector import ChangeSet, ChangeType, DetectedChange


@pytest.fixture
def sample_changes() -> ChangeSet:
    """Create sample changes for testing."""
    return ChangeSet(
        changes=[
            DetectedChange(
                change_type=ChangeType.MODIFIED,
                file_path="src/module_a.py",
                old_path=None,
            ),
            DetectedChange(
                change_type=ChangeType.ADDED,
                file_path="src/new_file.py",
                old_path=None,
            ),
        ]
    )


@pytest.fixture
def sample_manifest() -> AnalysisManifest:
    """Create a sample manifest with expanded components."""
    return AnalysisManifest(
        repo_state_hash="abc123",
        base_commit="def456",
        file_to_component={
            "src/module_a.py": "ComponentA",
            "src/module_a_utils.py": "ComponentA",
            "src/module_b.py": "ComponentB",
        },
        expanded_components=["ComponentA", "ComponentB"],
    )


@pytest.fixture
def sample_analysis() -> AnalysisInsights:
    """Create a sample analysis."""
    return AnalysisInsights(
        description="Test project",
        components=[
            Component(
                name="ComponentA",
                description="First component",
                key_entities=[],
                assigned_files=["src/module_a.py", "src/module_a_utils.py"],
                source_cluster_ids=[1],
            ),
            Component(
                name="ComponentB",
                description="Second component",
                key_entities=[],
                assigned_files=["src/module_b.py"],
                source_cluster_ids=[2],
            ),
        ],
        components_relations=[],
    )


class TestAnalyzeExpandedComponentImpacts:
    """Tests for analyze_expanded_component_impacts function."""

    def test_analyze_expanded_components_with_changes(self, sample_changes, sample_manifest):
        """Test impact analysis for expanded components with changes."""
        result = analyze_expanded_component_impacts(sample_changes, sample_manifest, None)

        assert "ComponentA" in result
        assert "ComponentB" not in result  # No changes for ComponentB
        assert result["ComponentA"].action == UpdateAction.UPDATE_COMPONENTS

    def test_analyze_empty_manifest_returns_empty(self, sample_changes):
        """Test that empty manifest returns empty results."""
        empty_manifest = AnalysisManifest(
            repo_state_hash="",
            base_commit="",
            file_to_component={},
            expanded_components=[],
        )
        result = analyze_expanded_component_impacts(sample_changes, empty_manifest, None)

        assert result == {}

    def test_analyze_no_expanded_components(self, sample_changes):
        """Test with manifest that has no expanded components."""
        manifest = AnalysisManifest(
            repo_state_hash="abc123",
            base_commit="def456",
            file_to_component={"src/file.py": "ComponentA"},
            expanded_components=[],  # No expanded components
        )

        result = analyze_expanded_component_impacts(sample_changes, manifest, None)

        assert result == {}

    def test_analyze_component_with_no_files(self, sample_changes):
        """Test component that has no files assigned."""
        manifest = AnalysisManifest(
            repo_state_hash="abc123",
            base_commit="def456",
            file_to_component={},
            expanded_components=["ComponentA"],
        )

        result = analyze_expanded_component_impacts(sample_changes, manifest, None)

        assert result == {}

    def test_analyze_no_matching_changes(self, sample_manifest):
        """Test when changes don't match any component files."""
        changes = ChangeSet(
            changes=[
                DetectedChange(
                    change_type=ChangeType.MODIFIED,
                    file_path="other/file.py",
                    old_path=None,
                )
            ]
        )

        result = analyze_expanded_component_impacts(changes, sample_manifest, None)

        assert result == {}

    def test_analyze_multiple_components_with_changes(self):
        """Test analysis when multiple components have changes."""
        changes = ChangeSet(
            changes=[
                DetectedChange(
                    change_type=ChangeType.MODIFIED,
                    file_path="src/module_a.py",
                    old_path=None,
                ),
                DetectedChange(
                    change_type=ChangeType.MODIFIED,
                    file_path="src/module_b.py",
                    old_path=None,
                ),
            ]
        )
        manifest = AnalysisManifest(
            repo_state_hash="abc123",
            base_commit="def456",
            file_to_component={
                "src/module_a.py": "ComponentA",
                "src/module_b.py": "ComponentB",
            },
            expanded_components=["ComponentA", "ComponentB"],
        )

        result = analyze_expanded_component_impacts(changes, manifest, None)

        assert "ComponentA" in result
        assert "ComponentB" in result


class TestRunScopedComponentImpacts:
    """Tests for run_scoped_component_impacts function."""

    def test_empty_components_returns_early(self, sample_analysis, sample_manifest, tmp_path, caplog):
        """Test that empty components list returns early."""
        import logging

        with caplog.at_level(logging.INFO):
            run_scoped_component_impacts(
                components=set(),
                component_impacts={},
                changes=ChangeSet(),
                analysis=sample_analysis,
                manifest=sample_manifest,
                output_dir=tmp_path,
                static_analysis=None,
                repo_dir=tmp_path,
            )

        # Should return early with no log messages about components
        assert "Scoped Impact" not in caplog.text

    def test_component_without_impact_is_skipped(self, sample_analysis, sample_manifest, tmp_path, caplog):
        """Test that components without impact data are skipped."""
        import logging

        # ComponentB has no entry in component_impacts dict
        impact_a = ChangeImpact(
            action=UpdateAction.NONE,
            dirty_components=set(),
            added_files=[],
            deleted_files=[],
            renames={},
        )
        component_impacts = {"ComponentA": impact_a}

        with caplog.at_level(logging.INFO):
            run_scoped_component_impacts(
                components={"ComponentA", "ComponentB"},  # ComponentB has no impact
                component_impacts=component_impacts,
                changes=ChangeSet(),
                analysis=sample_analysis,
                manifest=sample_manifest,
                output_dir=tmp_path,
                static_analysis=None,
                repo_dir=tmp_path,
            )

        # ComponentA should be logged, ComponentB should be skipped
        assert "ComponentA" in caplog.text
        assert "ComponentB" not in caplog.text

    def test_logging_of_component_impact(self, sample_analysis, sample_manifest, tmp_path, caplog):
        """Test that component impacts are logged correctly."""
        import logging

        impact = ChangeImpact(
            action=UpdateAction.PATCH_PATHS,
            dirty_components={"ComponentA"},
            added_files=["src/new.py"],
            deleted_files=[],
            renames={},
        )

        with caplog.at_level(logging.INFO):
            # Patch handle_scoped_component_update to avoid LLM calls
            with patch("diagram_analysis.incremental.scoped_analysis.handle_scoped_component_update"):
                run_scoped_component_impacts(
                    components={"ComponentA"},
                    component_impacts={"ComponentA": impact},
                    changes=ChangeSet(),
                    analysis=sample_analysis,
                    manifest=sample_manifest,
                    output_dir=tmp_path,
                    static_analysis=None,
                    repo_dir=tmp_path,
                )

        assert "Scoped Impact" in caplog.text
        assert "ComponentA" in caplog.text
        assert "patch_paths" in caplog.text

    def test_multiple_components_logged(self, sample_analysis, sample_manifest, tmp_path, caplog):
        """Test that multiple components are processed in sorted order."""
        import logging

        impact_a = ChangeImpact(
            action=UpdateAction.NONE,
            dirty_components=set(),
            added_files=[],
            deleted_files=[],
            renames={},
        )
        impact_b = ChangeImpact(
            action=UpdateAction.NONE,
            dirty_components=set(),
            added_files=[],
            deleted_files=[],
            renames={},
        )

        with caplog.at_level(logging.INFO):
            run_scoped_component_impacts(
                components={"ComponentB", "ComponentA"},  # Unsorted
                component_impacts={"ComponentA": impact_a, "ComponentB": impact_b},
                changes=ChangeSet(),
                analysis=sample_analysis,
                manifest=sample_manifest,
                output_dir=tmp_path,
                static_analysis=None,
                repo_dir=tmp_path,
            )

        # Both components should be logged
        log_text = caplog.text
        assert "ComponentA" in log_text
        assert "ComponentB" in log_text

    def test_only_update_or_patch_triggers_handling(self, sample_analysis, sample_manifest, tmp_path):
        """Test that only UPDATE_COMPONENTS or PATCH_PATHS actions trigger handle_scoped_component_update."""
        impact = ChangeImpact(
            action=UpdateAction.NONE,
            dirty_components=set(),
            added_files=[],
            deleted_files=[],
            renames={},
        )

        with patch("diagram_analysis.incremental.scoped_analysis.handle_scoped_component_update") as mock_handle:
            run_scoped_component_impacts(
                components={"ComponentA"},
                component_impacts={"ComponentA": impact},
                changes=ChangeSet(),
                analysis=sample_analysis,
                manifest=sample_manifest,
                output_dir=tmp_path,
                static_analysis=None,
                repo_dir=tmp_path,
            )

        # handle_scoped_component_update should not be called for NONE action
        mock_handle.assert_not_called()
