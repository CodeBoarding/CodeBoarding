"""Tests for incremental analysis functionality."""

import pytest
from pathlib import Path

from diagram_analysis.manifest import (
    AnalysisManifest,
    build_manifest_from_analysis,
    save_manifest,
    load_manifest,
)
from diagram_analysis.incremental import (
    ChangeImpact,
    UpdateAction,
    analyze_impact,
    patch_paths_in_analysis,
    patch_paths_in_manifest,
)
from diagram_analysis.incremental.impact_analyzer import _filter_changes_for_scope
from repo_utils.change_detector import (
    ChangeSet,
    ChangeType,
    DetectedChange,
    _parse_status_line,
)
from agents.agent_responses import AnalysisInsights, Component, Relation, SourceCodeReference


@pytest.fixture
def sample_analysis() -> AnalysisInsights:
    """Create a sample analysis for testing."""
    return AnalysisInsights(
        description="Test project description",
        components=[
            Component(
                name="ComponentA",
                description="First component",
                key_entities=[
                    SourceCodeReference(
                        qualified_name="module_a.ClassA",
                        reference_file="src/module_a.py",
                        reference_start_line=10,
                        reference_end_line=50,
                    )
                ],
                assigned_files=["src/module_a.py", "src/module_a_utils.py"],
                source_cluster_ids=[1, 2],
            ),
            Component(
                name="ComponentB",
                description="Second component",
                key_entities=[
                    SourceCodeReference(
                        qualified_name="module_b.ClassB",
                        reference_file="src/module_b.py",
                        reference_start_line=None,
                        reference_end_line=None,
                    )
                ],
                assigned_files=["src/module_b.py"],
                source_cluster_ids=[3],
            ),
        ],
        components_relations=[Relation(relation="calls", src_name="ComponentA", dst_name="ComponentB")],
    )


@pytest.fixture
def sample_manifest(sample_analysis: AnalysisInsights) -> AnalysisManifest:
    """Create a sample manifest for testing."""
    return build_manifest_from_analysis(
        analysis=sample_analysis,
        repo_state_hash="abc1234_deadbeef",
        base_commit="abc1234567890",
        expanded_components=["ComponentA"],
    )


class TestManifest:
    def test_build_manifest_from_analysis(self, sample_analysis: AnalysisInsights):
        manifest = build_manifest_from_analysis(
            analysis=sample_analysis,
            repo_state_hash="test_hash",
            base_commit="test_commit",
        )

        assert manifest.repo_state_hash == "test_hash"
        assert manifest.base_commit == "test_commit"
        assert len(manifest.file_to_component) == 3
        assert manifest.file_to_component["src/module_a.py"] == "ComponentA"
        assert manifest.file_to_component["src/module_b.py"] == "ComponentB"

    def test_get_component_for_file(self, sample_manifest: AnalysisManifest):
        assert sample_manifest.get_component_for_file("src/module_a.py") == "ComponentA"
        assert sample_manifest.get_component_for_file("src/module_b.py") == "ComponentB"
        assert sample_manifest.get_component_for_file("nonexistent.py") is None

    def test_update_file_path(self, sample_manifest: AnalysisManifest):
        result = sample_manifest.update_file_path("src/module_a.py", "src/renamed_a.py")
        assert result is True
        assert sample_manifest.get_component_for_file("src/renamed_a.py") == "ComponentA"
        assert sample_manifest.get_component_for_file("src/module_a.py") is None

    def test_save_and_load_manifest(self, sample_manifest: AnalysisManifest, tmp_path: Path):
        save_manifest(sample_manifest, tmp_path)
        loaded = load_manifest(tmp_path)

        assert loaded is not None
        assert loaded.repo_state_hash == sample_manifest.repo_state_hash
        assert loaded.file_to_component == sample_manifest.file_to_component


class TestChangeDetector:
    def test_parse_modified_line(self):
        change = _parse_status_line("M\tsrc/file.py")
        assert change is not None
        assert change.change_type == ChangeType.MODIFIED
        assert change.file_path == "src/file.py"

    def test_parse_added_line(self):
        change = _parse_status_line("A\tnew_file.py")
        assert change is not None
        assert change.change_type == ChangeType.ADDED
        assert change.file_path == "new_file.py"

    def test_parse_deleted_line(self):
        change = _parse_status_line("D\told_file.py")
        assert change is not None
        assert change.change_type == ChangeType.DELETED
        assert change.file_path == "old_file.py"

    def test_parse_renamed_line(self):
        change = _parse_status_line("R100\told.py\tnew.py")
        assert change is not None
        assert change.change_type == ChangeType.RENAMED
        assert change.file_path == "new.py"
        assert change.old_path == "old.py"
        assert change.similarity == 100

    def test_parse_renamed_with_low_similarity(self):
        change = _parse_status_line("R075\told.py\tnew.py")
        assert change is not None
        assert change.similarity == 75


class TestChangeSet:
    def test_empty_changeset(self):
        cs = ChangeSet()
        assert cs.is_empty()
        assert not cs.has_structural_changes()

    def test_rename_only(self):
        cs = ChangeSet(changes=[DetectedChange(ChangeType.RENAMED, "new.py", "old.py", 100)])
        assert cs.has_only_renames()
        assert cs.renames == {"old.py": "new.py"}

    def test_mixed_changes(self):
        cs = ChangeSet(
            changes=[
                DetectedChange(ChangeType.MODIFIED, "src/mod.py"),
                DetectedChange(ChangeType.ADDED, "src/new.py"),
            ]
        )
        assert not cs.has_only_renames()
        assert cs.has_structural_changes()
        assert cs.modified_files == ["src/mod.py"]
        assert cs.added_files == ["src/new.py"]


class TestImpactAnalysis:
    def test_no_changes(self, sample_manifest: AnalysisManifest):
        changes = ChangeSet()
        impact = analyze_impact(changes, sample_manifest)

        assert impact.action == UpdateAction.NONE

    def test_rename_only_action(self, sample_manifest: AnalysisManifest):
        changes = ChangeSet(changes=[DetectedChange(ChangeType.RENAMED, "src/renamed_a.py", "src/module_a.py", 100)])
        impact = analyze_impact(changes, sample_manifest)

        assert impact.action == UpdateAction.PATCH_PATHS
        assert "ComponentA" in impact.dirty_components
        assert impact.renames == {"src/module_a.py": "src/renamed_a.py"}

    def test_modified_file_action(self, sample_manifest: AnalysisManifest):
        changes = ChangeSet(changes=[DetectedChange(ChangeType.MODIFIED, "src/module_a.py")])
        impact = analyze_impact(changes, sample_manifest)

        assert impact.action == UpdateAction.UPDATE_COMPONENTS
        assert "ComponentA" in impact.dirty_components

    def test_new_file_unassigned(self, sample_manifest: AnalysisManifest):
        changes = ChangeSet(changes=[DetectedChange(ChangeType.ADDED, "src/new_module.py")])
        impact = analyze_impact(changes, sample_manifest)

        assert "src/new_module.py" in impact.unassigned_files


class TestPathPatching:
    def test_patch_analysis_paths(self, sample_analysis: AnalysisInsights):
        renames = {"src/module_a.py": "src/renamed_a.py"}
        patched = patch_paths_in_analysis(sample_analysis, renames)

        # Check assigned_files updated
        assert "src/renamed_a.py" in patched.components[0].assigned_files
        assert "src/module_a.py" not in patched.components[0].assigned_files

        # Check key_entities reference_file updated
        assert patched.components[0].key_entities[0].reference_file == "src/renamed_a.py"

    def test_patch_manifest_paths(self, sample_manifest: AnalysisManifest):
        renames = {"src/module_a.py": "src/renamed_a.py"}
        patched = patch_paths_in_manifest(sample_manifest, renames)

        assert patched.get_component_for_file("src/renamed_a.py") == "ComponentA"
        assert patched.get_component_for_file("src/module_a.py") is None


class TestBugFixes:
    """Tests for specific bug fixes in incremental analysis."""

    def test_recompute_dirty_components_excludes_rename_new_paths(self):
        """Bug 2: recompute_dirty_components should not include rename new paths
        (values) in the changed_files set. New paths are not yet in the manifest
        and would cause incorrect lookups.
        """
        from diagram_analysis.incremental.updater import IncrementalUpdater

        updater = IncrementalUpdater(
            repo_dir=Path("/fake/repo"),
            output_dir=Path("/fake/output"),
        )
        updater.manifest = AnalysisManifest(
            repo_state_hash="test",
            base_commit="abc123",
            file_to_component={
                "src/old_name.py": "ComponentA",
                "src/module_b.py": "ComponentB",
            },
            expanded_components=[],
        )
        updater.analysis = AnalysisInsights(
            description="Test",
            components=[
                Component(
                    name="ComponentA",
                    description="A",
                    key_entities=[],
                    assigned_files=["src/old_name.py"],
                    source_cluster_ids=[1],
                ),
                Component(
                    name="ComponentB",
                    description="B",
                    key_entities=[],
                    assigned_files=["src/module_b.py"],
                    source_cluster_ids=[2],
                ),
            ],
            components_relations=[],
        )
        updater.impact = ChangeImpact(
            renames={"src/old_name.py": "src/new_name.py"},
            modified_files=[],
            added_files=[],
            deleted_files=[],
            dirty_components={"ComponentA"},
            components_needing_reexpansion=set(),
        )

        # Mock static analysis with empty cluster results
        from unittest.mock import MagicMock

        mock_static = MagicMock()
        mock_static.get_languages.return_value = []

        updater.recompute_dirty_components(mock_static)

        # ComponentA should still be dirty (old_name.py maps to it via manifest)
        assert "ComponentA" in updater.impact.dirty_components
        # The rename new path "src/new_name.py" should NOT have caused
        # ComponentB (or any other component) to be incorrectly added
        # Only ComponentA should be dirty since only old_name.py is in the manifest
        assert "ComponentB" not in updater.impact.dirty_components

    def test_filter_changes_for_scope_strict_membership(self):
        """Bug 3: _filter_changes_for_scope should only include changes for files
        that are directly in the scope set, not files that merely share a parent
        directory with scoped files.
        """
        scope_files = {"src/module_a.py", "src/module_a_utils.py"}

        # A change to module_b.py in the same directory should NOT be in scope
        changes = ChangeSet(
            changes=[
                DetectedChange(ChangeType.MODIFIED, "src/module_a.py"),
                DetectedChange(ChangeType.MODIFIED, "src/module_b.py"),
            ]
        )

        scoped = _filter_changes_for_scope(changes, scope_files)

        # Only module_a.py should be included, not module_b.py
        assert len(scoped.changes) == 1
        assert scoped.changes[0].file_path == "src/module_a.py"

    def test_filter_changes_for_scope_rename_in_scope(self):
        """Bug 3: Renames should be included if old or new path is in scope."""
        scope_files = {"src/module_a.py", "src/module_a_utils.py"}

        changes = ChangeSet(
            changes=[
                DetectedChange(ChangeType.RENAMED, "src/module_a_renamed.py", "src/module_a.py", 100),
            ]
        )

        scoped = _filter_changes_for_scope(changes, scope_files)

        # Rename should be included because old_path is in scope
        assert len(scoped.changes) == 1

    def test_filter_changes_for_scope_excludes_sibling_directory_files(self):
        """Bug 3: Files in sibling directories or same parent dir but different
        component should be excluded from scope.
        """
        scope_files = {"src/auth/login.py", "src/auth/session.py"}

        changes = ChangeSet(
            changes=[
                DetectedChange(ChangeType.MODIFIED, "src/auth/login.py"),
                DetectedChange(ChangeType.MODIFIED, "src/auth/register.py"),  # same dir, different component
                DetectedChange(ChangeType.MODIFIED, "src/db/models.py"),  # different dir
            ]
        )

        scoped = _filter_changes_for_scope(changes, scope_files)

        # Only login.py should be included (it's directly in scope)
        assert len(scoped.changes) == 1
        assert scoped.changes[0].file_path == "src/auth/login.py"
