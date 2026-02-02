"""Integration tests for incremental analysis functionality.

These tests simulate real-world scenarios with git operations.
"""

import json
import os
import pytest
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from diagram_analysis.manifest import (
    AnalysisManifest,
    save_manifest,
    load_manifest,
    MANIFEST_FILENAME,
)
from diagram_analysis.incremental_analyzer import (
    IncrementalUpdater,
    UpdateAction,
    load_analysis,
    save_analysis,
)
from repo_utils.change_detector import detect_changes, detect_changes_from_commit, get_current_commit
from agents.agent_responses import AnalysisInsights, Component, Relation, SourceCodeReference


def run_git(repo_dir: Path, *args: str) -> str:
    """Run a git command in the repo directory."""
    result = subprocess.run(
        ["git"] + list(args),
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path):
    """Create a temporary git repository with some files."""
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()

    # Initialize git repo
    run_git(repo_dir, "init")
    run_git(repo_dir, "config", "user.email", "test@test.com")
    run_git(repo_dir, "config", "user.name", "Test User")

    # Create source files
    src_dir = repo_dir / "src"
    src_dir.mkdir()

    (src_dir / "module_a.py").write_text("def func_a():\n    pass\n")
    (src_dir / "module_b.py").write_text("def func_b():\n    pass\n")

    # Initial commit
    run_git(repo_dir, "add", ".")
    run_git(repo_dir, "commit", "-m", "Initial commit")

    return repo_dir


@pytest.fixture
def sample_analysis() -> AnalysisInsights:
    """Create a sample analysis for testing."""
    return AnalysisInsights(
        description="Test project",
        components=[
            Component(
                name="ComponentA",
                description="First component",
                key_entities=[
                    SourceCodeReference(
                        qualified_name="module_a.func_a",
                        reference_file="src/module_a.py",
                        reference_start_line=1,
                        reference_end_line=2,
                    )
                ],
                assigned_files=["src/module_a.py"],
                source_cluster_ids=[1],
            ),
            Component(
                name="ComponentB",
                description="Second component",
                key_entities=[
                    SourceCodeReference(
                        qualified_name="module_b.func_b",
                        reference_file="src/module_b.py",
                        reference_start_line=1,
                        reference_end_line=2,
                    )
                ],
                assigned_files=["src/module_b.py"],
                source_cluster_ids=[2],
            ),
        ],
        components_relations=[Relation(relation="calls", src_name="ComponentA", dst_name="ComponentB")],
    )


class TestGitChangeDetection:
    """Test change detection with real git operations."""

    def test_detect_no_changes(self, git_repo: Path):
        """No changes should be detected on a clean repo."""
        commit = get_current_commit(git_repo)
        changes = detect_changes_from_commit(git_repo, commit)

        assert changes.is_empty()

    def test_detect_modified_file(self, git_repo: Path):
        """Detect modified files."""
        base_commit = get_current_commit(git_repo)

        # Modify a file
        (git_repo / "src" / "module_a.py").write_text("def func_a():\n    return 42\n")
        run_git(git_repo, "add", ".")
        run_git(git_repo, "commit", "-m", "Modify module_a")

        changes = detect_changes_from_commit(git_repo, base_commit)

        assert not changes.is_empty()
        assert "src/module_a.py" in changes.modified_files

    def test_detect_renamed_file(self, git_repo: Path):
        """Detect renamed files with proper old/new path tracking."""
        base_commit = get_current_commit(git_repo)

        # Rename a file
        run_git(git_repo, "mv", "src/module_a.py", "src/renamed_a.py")
        run_git(git_repo, "commit", "-m", "Rename module_a")

        changes = detect_changes_from_commit(git_repo, base_commit)

        assert not changes.is_empty()
        assert "src/module_a.py" in changes.renames
        assert changes.renames["src/module_a.py"] == "src/renamed_a.py"
        assert changes.has_only_renames()

    def test_detect_added_file(self, git_repo: Path):
        """Detect newly added files."""
        base_commit = get_current_commit(git_repo)

        # Add a new file
        (git_repo / "src" / "module_c.py").write_text("def func_c():\n    pass\n")
        run_git(git_repo, "add", ".")
        run_git(git_repo, "commit", "-m", "Add module_c")

        changes = detect_changes_from_commit(git_repo, base_commit)

        assert not changes.is_empty()
        assert "src/module_c.py" in changes.added_files
        assert changes.has_structural_changes()

    def test_detect_deleted_file(self, git_repo: Path):
        """Detect deleted files."""
        base_commit = get_current_commit(git_repo)

        # Delete a file
        run_git(git_repo, "rm", "src/module_b.py")
        run_git(git_repo, "commit", "-m", "Delete module_b")

        changes = detect_changes_from_commit(git_repo, base_commit)

        assert not changes.is_empty()
        assert "src/module_b.py" in changes.deleted_files
        assert changes.has_structural_changes()


class TestIncrementalUpdaterIntegration:
    """Integration tests for the full incremental update flow."""

    def test_incremental_not_possible_without_manifest(self, git_repo: Path, sample_analysis: AnalysisInsights):
        """Should fall back to full analysis without a manifest."""
        output_dir = git_repo / ".codeboarding"
        output_dir.mkdir()

        # Save analysis but NOT manifest
        save_analysis(sample_analysis, output_dir)

        updater = IncrementalUpdater(repo_dir=git_repo, output_dir=output_dir)

        assert not updater.can_run_incremental()

    def test_incremental_rename_only(self, git_repo: Path, sample_analysis: AnalysisInsights):
        """Pure rename should use PATCH_PATHS action (no LLM)."""
        output_dir = git_repo / ".codeboarding"
        output_dir.mkdir()

        base_commit = get_current_commit(git_repo)

        # Save analysis and manifest
        save_analysis(sample_analysis, output_dir)
        manifest = AnalysisManifest(
            repo_state_hash="test_hash",
            base_commit=base_commit,
            file_to_component={
                "src/module_a.py": "ComponentA",
                "src/module_b.py": "ComponentB",
            },
            expanded_components=[],
        )
        save_manifest(manifest, output_dir)

        # Rename a file
        run_git(git_repo, "mv", "src/module_a.py", "src/renamed_a.py")
        run_git(git_repo, "commit", "-m", "Rename module_a")

        # Run incremental updater
        updater = IncrementalUpdater(repo_dir=git_repo, output_dir=output_dir)
        assert updater.can_run_incremental()

        impact = updater.analyze()
        assert impact.action == UpdateAction.PATCH_PATHS
        assert "src/module_a.py" in impact.renames

        # Execute the update
        result = updater.execute()
        assert result is True

        # Verify the analysis was patched
        updated_analysis = load_analysis(output_dir)
        assert updated_analysis is not None

        # Check ComponentA has the renamed file
        comp_a = next(c for c in updated_analysis.components if c.name == "ComponentA")
        assert "src/renamed_a.py" in comp_a.assigned_files
        assert "src/module_a.py" not in comp_a.assigned_files

        # Check key_entity was updated
        assert comp_a.key_entities[0].reference_file == "src/renamed_a.py"

        # Verify manifest was updated
        updated_manifest = load_manifest(output_dir)
        assert updated_manifest is not None
        assert updated_manifest.get_component_for_file("src/renamed_a.py") == "ComponentA"
        assert updated_manifest.get_component_for_file("src/module_a.py") is None

    def test_incremental_no_changes(self, git_repo: Path, sample_analysis: AnalysisInsights):
        """No changes should result in NONE action."""
        output_dir = git_repo / ".codeboarding"
        output_dir.mkdir()

        base_commit = get_current_commit(git_repo)

        # Save analysis and manifest
        save_analysis(sample_analysis, output_dir)
        manifest = AnalysisManifest(
            repo_state_hash="test_hash",
            base_commit=base_commit,
            file_to_component={
                "src/module_a.py": "ComponentA",
                "src/module_b.py": "ComponentB",
            },
            expanded_components=[],
        )
        save_manifest(manifest, output_dir)

        # No changes made

        updater = IncrementalUpdater(repo_dir=git_repo, output_dir=output_dir)
        assert updater.can_run_incremental()

        impact = updater.analyze()
        assert impact.action == UpdateAction.NONE

    def test_incremental_modified_file_maps_to_component(self, git_repo: Path, sample_analysis: AnalysisInsights):
        """Modified file should map to correct dirty component."""
        output_dir = git_repo / ".codeboarding"
        output_dir.mkdir()

        base_commit = get_current_commit(git_repo)

        # Save analysis and manifest
        save_analysis(sample_analysis, output_dir)
        manifest = AnalysisManifest(
            repo_state_hash="test_hash",
            base_commit=base_commit,
            file_to_component={
                "src/module_a.py": "ComponentA",
                "src/module_b.py": "ComponentB",
            },
            expanded_components=[],
        )
        save_manifest(manifest, output_dir)

        # Modify module_a
        (git_repo / "src" / "module_a.py").write_text("def func_a():\n    return 42\n")
        run_git(git_repo, "add", ".")
        run_git(git_repo, "commit", "-m", "Modify module_a")

        updater = IncrementalUpdater(repo_dir=git_repo, output_dir=output_dir)
        assert updater.can_run_incremental()
        impact = updater.analyze()

        assert "ComponentA" in impact.dirty_components
        assert "ComponentB" not in impact.dirty_components
        assert impact.action == UpdateAction.UPDATE_COMPONENTS


class TestEndToEndScenarios:
    """End-to-end tests for common developer workflows."""

    def test_workflow_rename_refactor(self, git_repo: Path, sample_analysis: AnalysisInsights):
        """
        Scenario: Developer renames a file as part of refactoring.
        Expected: Fast update (no LLM), paths updated correctly.
        """
        output_dir = git_repo / ".codeboarding"
        output_dir.mkdir()

        base_commit = get_current_commit(git_repo)

        # Initial state
        save_analysis(sample_analysis, output_dir)
        manifest = AnalysisManifest(
            repo_state_hash="test_hash",
            base_commit=base_commit,
            file_to_component={
                "src/module_a.py": "ComponentA",
                "src/module_b.py": "ComponentB",
            },
        )
        save_manifest(manifest, output_dir)

        # Developer renames file
        run_git(git_repo, "mv", "src/module_a.py", "src/component_a.py")
        run_git(git_repo, "commit", "-m", "Rename for clarity")

        # Run incremental update
        updater = IncrementalUpdater(repo_dir=git_repo, output_dir=output_dir)
        assert updater.can_run_incremental()

        impact = updater.analyze()
        assert impact.action == UpdateAction.PATCH_PATHS

        success = updater.execute()
        assert success

        # Verify results
        analysis = load_analysis(output_dir)
        comp_a = next(c for c in analysis.components if c.name == "ComponentA")
        assert "src/component_a.py" in comp_a.assigned_files

    def test_workflow_add_file_to_existing_component(self, git_repo: Path, sample_analysis: AnalysisInsights):
        """
        Scenario: Developer adds a new file in an existing component's directory.
        Expected: File assigned to that component.
        """
        output_dir = git_repo / ".codeboarding"
        output_dir.mkdir()

        base_commit = get_current_commit(git_repo)

        save_analysis(sample_analysis, output_dir)
        manifest = AnalysisManifest(
            repo_state_hash="test_hash",
            base_commit=base_commit,
            file_to_component={
                "src/module_a.py": "ComponentA",
                "src/module_b.py": "ComponentB",
            },
        )
        save_manifest(manifest, output_dir)

        # Add a helper file for module_a
        (git_repo / "src" / "module_a_helper.py").write_text("def helper():\n    pass\n")
        run_git(git_repo, "add", ".")
        run_git(git_repo, "commit", "-m", "Add helper")

        updater = IncrementalUpdater(repo_dir=git_repo, output_dir=output_dir)
        assert updater.can_run_incremental()
        impact = updater.analyze()

        # New file should be in unassigned
        assert "src/module_a_helper.py" in impact.unassigned_files
