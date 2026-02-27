"""Tests for the incremental analyzer module."""

import pytest
from agents.agent_responses import AnalysisInsights, Component, FileMethodGroup, SourceCodeReference
from diagram_analysis.incremental import patch_sub_analysis


def _get_file_paths(component: Component) -> list[str]:
    """Helper to extract file paths from a component's file_methods."""
    return [fg.file_path for fg in component.file_methods]


class TestPatchSubAnalysis:
    """Test the patch_sub_analysis function."""

    def test_removes_deleted_files_from_file_methods(self):
        """Deleted files should be removed from file_methods."""
        sub = AnalysisInsights(
            description="Test",
            components=[
                Component(
                    name="Test Component",
                    description="Test",
                    key_entities=[],
                    file_methods=[
                        FileMethodGroup(file_path="file1.py"),
                        FileMethodGroup(file_path="file2.py"),
                        FileMethodGroup(file_path="file3.py"),
                    ],
                ),
            ],
            components_relations=[],
        )

        changed = patch_sub_analysis(sub, deleted_files=["file2.py"], renames={})

        assert changed is True
        assert _get_file_paths(sub.components[0]) == ["file1.py", "file3.py"]

    def test_removes_key_entities_referencing_deleted_files(self):
        """Key entities referencing deleted files should be removed."""
        sub = AnalysisInsights(
            description="Test",
            components=[
                Component(
                    name="Test Component",
                    description="Test",
                    key_entities=[
                        SourceCodeReference(
                            qualified_name="mod.Deleted",
                            reference_file="deleted.py",
                            reference_start_line=1,
                            reference_end_line=10,
                        ),
                        SourceCodeReference(
                            qualified_name="mod.Kept",
                            reference_file="kept.py",
                            reference_start_line=1,
                            reference_end_line=10,
                        ),
                    ],
                ),
            ],
            components_relations=[],
        )

        changed = patch_sub_analysis(sub, deleted_files=["deleted.py"], renames={})

        assert changed is True
        assert len(sub.components[0].key_entities) == 1
        assert sub.components[0].key_entities[0].qualified_name == "mod.Kept"

    def test_applies_renames_to_file_methods(self):
        """Renamed files should be updated in file_methods."""
        sub = AnalysisInsights(
            description="Test",
            components=[
                Component(
                    name="Test Component",
                    description="Test",
                    key_entities=[],
                    file_methods=[
                        FileMethodGroup(file_path="old_name.py"),
                        FileMethodGroup(file_path="other.py"),
                    ],
                ),
            ],
            components_relations=[],
        )

        changed = patch_sub_analysis(sub, deleted_files=[], renames={"old_name.py": "new_name.py"})

        assert changed is True
        assert _get_file_paths(sub.components[0]) == ["new_name.py", "other.py"]

    def test_applies_renames_to_key_entities(self):
        """Renamed files should be updated in key_entities."""
        sub = AnalysisInsights(
            description="Test",
            components=[
                Component(
                    name="Test Component",
                    description="Test",
                    key_entities=[
                        SourceCodeReference(
                            qualified_name="mod.Class",
                            reference_file="old_name.py",
                            reference_start_line=1,
                            reference_end_line=10,
                        ),
                    ],
                ),
            ],
            components_relations=[],
        )

        changed = patch_sub_analysis(sub, deleted_files=[], renames={"old_name.py": "new_name.py"})

        assert changed is True
        assert sub.components[0].key_entities[0].reference_file == "new_name.py"

    def test_handles_path_with_repo_prefix(self):
        """Files with repos/ prefix should be matched correctly."""
        sub = AnalysisInsights(
            description="Test",
            components=[
                Component(
                    name="Test Component",
                    description="Test",
                    key_entities=[],
                    file_methods=[
                        FileMethodGroup(file_path="repos/MyProject/agents/file.py"),
                        FileMethodGroup(file_path="agents/file.py"),
                    ],
                ),
            ],
            components_relations=[],
        )

        # Delete using the relative path
        changed = patch_sub_analysis(sub, deleted_files=["agents/file.py"], renames={})

        assert changed is True
        assert _get_file_paths(sub.components[0]) == []

    def test_no_changes_returns_false(self):
        """When no changes are needed, should return False."""
        sub = AnalysisInsights(
            description="Test",
            components=[
                Component(
                    name="Test Component",
                    description="Test",
                    key_entities=[],
                    file_methods=[FileMethodGroup(file_path="file.py")],
                ),
            ],
            components_relations=[],
        )

        changed = patch_sub_analysis(sub, deleted_files=["other.py"], renames={"nonexistent.py": "new.py"})

        assert changed is False
        assert _get_file_paths(sub.components[0]) == ["file.py"]

    def test_combined_deletes_and_renames(self):
        """Should handle both deletes and renames in the same call."""
        sub = AnalysisInsights(
            description="Test",
            components=[
                Component(
                    name="Test Component",
                    description="Test",
                    key_entities=[
                        SourceCodeReference(
                            qualified_name="mod.Deleted",
                            reference_file="agents/prompts/claude_prompts_unidirectional.py",
                            reference_start_line=1,
                            reference_end_line=10,
                        ),
                        SourceCodeReference(
                            qualified_name="mod.Renamed",
                            reference_file="agents/prompts/abstract_prompt_factory.py",
                            reference_start_line=1,
                            reference_end_line=10,
                        ),
                    ],
                    file_methods=[
                        FileMethodGroup(file_path="agents/prompts/claude_prompts_unidirectional.py"),
                        FileMethodGroup(file_path="agents/prompts/gemini_flash_prompts_unidirectional.py"),
                        FileMethodGroup(file_path="agents/prompts/abstract_prompt_factory.py"),
                    ],
                ),
            ],
            components_relations=[],
        )

        changed = patch_sub_analysis(
            sub,
            deleted_files=[
                "agents/prompts/claude_prompts_unidirectional.py",
                "agents/prompts/gemini_flash_prompts_unidirectional.py",
            ],
            renames={"agents/prompts/abstract_prompt_factory.py": "agents/prompts/base_factory.py"},
        )

        assert changed is True
        assert _get_file_paths(sub.components[0]) == ["agents/prompts/base_factory.py"]
        assert len(sub.components[0].key_entities) == 1
        assert sub.components[0].key_entities[0].reference_file == "agents/prompts/base_factory.py"
