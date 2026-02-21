"""Tests for component checking utilities."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    hash_component_id,
    ROOT_PARENT_ID,
)
from diagram_analysis.incremental.component_checker import (
    is_expanded_component,
    component_has_only_renames,
    can_patch_sub_analysis,
    subcomponent_has_only_renames,
)
from diagram_analysis.incremental.models import ChangeImpact, UpdateAction
from diagram_analysis.manifest import AnalysisManifest


COMP_A_ID = hash_component_id(ROOT_PARENT_ID, "ComponentA")
COMP_B_ID = hash_component_id(ROOT_PARENT_ID, "ComponentB")


@pytest.fixture
def temp_output_dir(tmp_path: Path) -> Path:
    """Create a temporary output directory."""
    return tmp_path / "output"


@pytest.fixture
def sample_manifest() -> AnalysisManifest:
    """Create a sample manifest for testing."""
    return AnalysisManifest(
        repo_state_hash="abc1234",
        base_commit="abc1234567890",
        file_to_component={
            "src/module_a.py": COMP_A_ID,
            "src/module_b.py": COMP_B_ID,
        },
        expanded_components=[COMP_A_ID],
    )


@pytest.fixture
def sample_impact() -> ChangeImpact:
    """Create a sample change impact for testing."""
    return ChangeImpact(
        added_files=[],
        deleted_files=[],
        modified_files=[],
        renames={},
        dirty_components=set(),
        components_needing_reexpansion=set(),
        cross_boundary_changes=[],
        architecture_dirty=False,
        unassigned_files=[],
        action=UpdateAction.NONE,
        reason="",
    )


@pytest.fixture
def sample_analysis() -> AnalysisInsights:
    """Create a sample analysis for testing."""
    return AnalysisInsights(
        description="Test analysis",
        components=[
            Component(
                name="ComponentA",
                component_id=COMP_A_ID,
                description="Test component A",
                key_entities=[],
                assigned_files=["src/module_a.py"],
                source_cluster_ids=[1],
            ),
            Component(
                name="ComponentB",
                component_id=COMP_B_ID,
                description="Test component B",
                key_entities=[],
                assigned_files=["src/module_b.py"],
                source_cluster_ids=[2],
            ),
        ],
        components_relations=[],
    )


class TestIsExpandedComponent:
    """Tests for is_expanded_component function."""

    def test_returns_true_when_in_manifest(self, sample_manifest: AnalysisManifest, temp_output_dir: Path):
        """Test that function returns True when component is in manifest's expanded list."""
        result = is_expanded_component(COMP_A_ID, sample_manifest, temp_output_dir)
        assert result is True

    def test_returns_false_when_not_in_manifest(self, sample_manifest: AnalysisManifest, temp_output_dir: Path):
        """Test that function returns False when component is not in manifest's expanded list."""
        result = is_expanded_component(COMP_B_ID, sample_manifest, temp_output_dir)
        assert result is False

    @patch("diagram_analysis.incremental.component_checker.load_sub_analysis")
    def test_returns_true_when_sub_analysis_exists(self, mock_load, temp_output_dir: Path):
        """Test that function returns True when sub-analysis exists in unified file (fallback check)."""
        mock_load.return_value = AnalysisInsights(description="Sub", components=[], components_relations=[])

        result = is_expanded_component(COMP_A_ID, None, temp_output_dir)
        assert result is True

    def test_returns_false_when_no_manifest_and_no_file(self, temp_output_dir: Path):
        """Test that function returns False when no manifest and no file exists."""
        result = is_expanded_component(COMP_A_ID, None, temp_output_dir)
        assert result is False

    @patch("diagram_analysis.incremental.component_checker.load_sub_analysis")
    def test_handles_special_characters_in_name(self, mock_load, temp_output_dir: Path):
        """Test that function handles component names with special characters."""
        mock_load.return_value = AnalysisInsights(description="Sub", components=[], components_relations=[])

        result = is_expanded_component("my/component:test", None, temp_output_dir)
        assert result is True


class TestComponentHasOnlyRenames:
    """Tests for component_has_only_renames function."""

    def test_returns_false_with_no_impact(self, sample_manifest: AnalysisManifest):
        """Test that function returns False when no impact is provided."""
        result = component_has_only_renames(COMP_A_ID, sample_manifest, None)
        assert result is False

    def test_returns_false_with_no_manifest(self, sample_impact: ChangeImpact):
        """Test that function returns False when no manifest is provided."""
        result = component_has_only_renames(COMP_A_ID, None, sample_impact)
        assert result is False

    def test_returns_false_when_no_changes(self, sample_manifest: AnalysisManifest, sample_impact: ChangeImpact):
        """Test that function returns False when no structural changes exist."""
        result = component_has_only_renames(COMP_A_ID, sample_manifest, sample_impact)
        assert result is False

    def test_returns_true_for_rename_only(self, sample_manifest: AnalysisManifest):
        """Test that function returns True when changes are only renames."""
        impact = ChangeImpact(
            added_files=[],
            deleted_files=["src/module_a.py"],
            modified_files=["src/module_a_new.py"],
            renames={"src/module_a.py": "src/module_a_new.py"},
            dirty_components={COMP_A_ID},
            components_needing_reexpansion=set(),
            cross_boundary_changes=[],
            architecture_dirty=False,
            unassigned_files=[],
            action=UpdateAction.PATCH_PATHS,
            reason="Only renames",
        )

        result = component_has_only_renames(COMP_A_ID, sample_manifest, impact)
        assert result is True

    def test_returns_false_for_true_deletion(self, sample_manifest: AnalysisManifest):
        """Test that function returns False when files are truly deleted (not renamed)."""
        impact = ChangeImpact(
            added_files=[],
            deleted_files=["src/module_a.py"],
            modified_files=[],
            renames={},
            dirty_components={COMP_A_ID},
            components_needing_reexpansion=set(),
            cross_boundary_changes=[],
            architecture_dirty=False,
            unassigned_files=[],
            action=UpdateAction.UPDATE_COMPONENTS,
            reason="File deleted",
        )

        result = component_has_only_renames(COMP_A_ID, sample_manifest, impact)
        assert result is False

    def test_returns_false_for_mixed_changes(self, sample_manifest: AnalysisManifest):
        """Test that function returns False when there are both renames and other changes."""
        impact = ChangeImpact(
            added_files=[],
            deleted_files=["src/module_a.py"],
            modified_files=["src/module_a_new.py", "src/module_b.py"],
            renames={"src/module_a.py": "src/module_a_new.py"},
            dirty_components={COMP_A_ID, COMP_B_ID},
            components_needing_reexpansion=set(),
            cross_boundary_changes=[],
            architecture_dirty=False,
            unassigned_files=[],
            action=UpdateAction.UPDATE_COMPONENTS,
            reason="Mixed changes",
        )

        result = component_has_only_renames(COMP_B_ID, sample_manifest, impact)
        assert result is False


class TestCanPatchSubAnalysis:
    """Tests for can_patch_sub_analysis function."""

    def test_returns_false_with_no_analysis(
        self,
        sample_manifest: AnalysisManifest,
        sample_impact: ChangeImpact,
        temp_output_dir: Path,
    ):
        """Test that function returns False when no analysis is provided."""
        result = can_patch_sub_analysis(COMP_A_ID, sample_manifest, sample_impact, temp_output_dir, None)
        assert result is False

    def test_returns_false_with_no_manifest(
        self,
        sample_analysis: AnalysisInsights,
        sample_impact: ChangeImpact,
        temp_output_dir: Path,
    ):
        """Test that function returns False when no manifest is provided."""
        result = can_patch_sub_analysis(COMP_A_ID, None, sample_impact, temp_output_dir, sample_analysis)
        assert result is False

    def test_returns_false_when_component_not_found(
        self,
        sample_manifest: AnalysisManifest,
        sample_impact: ChangeImpact,
        sample_analysis: AnalysisInsights,
        temp_output_dir: Path,
    ):
        """Test that function returns False when component doesn't exist in analysis."""
        result = can_patch_sub_analysis(
            "NonExistent",
            sample_manifest,
            sample_impact,
            temp_output_dir,
            sample_analysis,
        )
        assert result is False

    def test_returns_false_when_no_sub_analysis_file(
        self,
        sample_manifest: AnalysisManifest,
        sample_impact: ChangeImpact,
        sample_analysis: AnalysisInsights,
        temp_output_dir: Path,
    ):
        """Test that function returns False when sub-analysis file doesn't exist."""
        result = can_patch_sub_analysis(
            COMP_A_ID,
            sample_manifest,
            sample_impact,
            temp_output_dir,
            sample_analysis,
        )
        assert result is False

    @patch("diagram_analysis.incremental.component_checker.load_sub_analysis")
    def test_returns_false_when_load_fails(
        self,
        mock_load,
        sample_manifest: AnalysisManifest,
        sample_impact: ChangeImpact,
        sample_analysis: AnalysisInsights,
        temp_output_dir: Path,
    ):
        """Test that function returns False when sub-analysis loading fails."""
        temp_output_dir.mkdir(parents=True, exist_ok=True)
        (temp_output_dir / "ComponentA.json").write_text('{"invalid": json}')
        mock_load.return_value = None

        result = can_patch_sub_analysis(
            COMP_A_ID,
            sample_manifest,
            sample_impact,
            temp_output_dir,
            sample_analysis,
        )
        assert result is False

    @patch("diagram_analysis.incremental.component_checker.load_sub_analysis")
    def test_returns_false_when_deletions_exist(
        self,
        mock_load,
        sample_manifest: AnalysisManifest,
        sample_analysis: AnalysisInsights,
        temp_output_dir: Path,
    ):
        """Test that function returns False when there are file deletions."""
        temp_output_dir.mkdir(parents=True, exist_ok=True)
        (temp_output_dir / "ComponentA.json").write_text('{"components": []}')

        # Create a mock sub-analysis with the component's files
        mock_sub_analysis = AnalysisInsights(
            description="Sub-analysis",
            components=[
                Component(
                    name="SubComponent",
                    description="Sub",
                    key_entities=[],
                    assigned_files=["src/module_a.py"],
                    source_cluster_ids=[1],
                )
            ],
            components_relations=[],
        )
        mock_load.return_value = mock_sub_analysis

        impact = ChangeImpact(
            added_files=[],
            deleted_files=["src/module_a.py"],
            modified_files=[],
            renames={},
            dirty_components={COMP_A_ID},
            components_needing_reexpansion={COMP_A_ID},
            cross_boundary_changes=[],
            architecture_dirty=False,
            unassigned_files=[],
            action=UpdateAction.UPDATE_COMPONENTS,
            reason="File deleted",
        )

        result = can_patch_sub_analysis(COMP_A_ID, sample_manifest, impact, temp_output_dir, sample_analysis)
        assert result is False

    @patch("diagram_analysis.incremental.component_checker.load_sub_analysis")
    def test_returns_true_when_only_renames(
        self,
        mock_load,
        sample_manifest: AnalysisManifest,
        sample_analysis: AnalysisInsights,
        temp_output_dir: Path,
    ):
        """Test that function returns True when changes are only renames."""
        temp_output_dir.mkdir(parents=True, exist_ok=True)
        (temp_output_dir / "ComponentA.json").write_text('{"components": []}')

        mock_sub_analysis = AnalysisInsights(
            description="Sub-analysis",
            components=[
                Component(
                    name="SubComponent",
                    description="Sub",
                    key_entities=[],
                    assigned_files=["src/module_a.py"],
                    source_cluster_ids=[1],
                )
            ],
            components_relations=[],
        )
        mock_load.return_value = mock_sub_analysis

        impact = ChangeImpact(
            added_files=[],
            deleted_files=[],
            modified_files=[],
            renames={"src/module_a.py": "src/module_a_new.py"},
            dirty_components=set(),
            components_needing_reexpansion=set(),
            cross_boundary_changes=[],
            architecture_dirty=False,
            unassigned_files=[],
            action=UpdateAction.PATCH_PATHS,
            reason="Only renames",
        )

        result = can_patch_sub_analysis(COMP_A_ID, sample_manifest, impact, temp_output_dir, sample_analysis)
        assert result is True


class TestSubcomponentHasOnlyRenames:
    """Tests for subcomponent_has_only_renames function."""

    def test_returns_false_with_no_impact(self, sample_analysis: AnalysisInsights):
        """Test that function returns False when no impact is provided."""
        result = subcomponent_has_only_renames(COMP_A_ID, sample_analysis, None)
        assert result is False

    def test_returns_false_when_no_changes(self, sample_analysis: AnalysisInsights, sample_impact: ChangeImpact):
        """Test that function returns False when no structural changes exist."""
        result = subcomponent_has_only_renames(COMP_A_ID, sample_analysis, sample_impact)
        assert result is False

    def test_returns_true_for_rename_only(self, sample_analysis: AnalysisInsights):
        """Test that function returns True when changes are only renames."""
        impact = ChangeImpact(
            added_files=[],
            deleted_files=["src/module_a.py"],
            modified_files=["src/module_a_new.py"],
            renames={"src/module_a.py": "src/module_a_new.py"},
            dirty_components=set(),
            components_needing_reexpansion=set(),
            cross_boundary_changes=[],
            architecture_dirty=False,
            unassigned_files=[],
            action=UpdateAction.PATCH_PATHS,
            reason="Only renames",
        )

        result = subcomponent_has_only_renames(COMP_A_ID, sample_analysis, impact)
        assert result is True

    def test_returns_false_for_true_deletion(self, sample_analysis: AnalysisInsights):
        """Test that function returns False when files are truly deleted."""
        impact = ChangeImpact(
            added_files=[],
            deleted_files=["src/module_a.py"],
            modified_files=[],
            renames={},
            dirty_components=set(),
            components_needing_reexpansion=set(),
            cross_boundary_changes=[],
            architecture_dirty=False,
            unassigned_files=[],
            action=UpdateAction.UPDATE_COMPONENTS,
            reason="File deleted",
        )

        result = subcomponent_has_only_renames(COMP_A_ID, sample_analysis, impact)
        assert result is False
