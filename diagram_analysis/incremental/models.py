"""
Core models for incremental analysis.

This module defines the data structures and enums used throughout
the incremental analysis system.
"""

from dataclasses import dataclass, field
from enum import Enum


class UpdateAction(Enum):
    """Recommended update action based on impact analysis."""

    NONE = "none"  # No changes detected
    PATCH_PATHS = "patch_paths"  # Rename only - no LLM needed
    UPDATE_COMPONENTS = "update_components"  # Re-run DetailsAgent for specific components
    UPDATE_ARCHITECTURE = "update_architecture"  # Re-run AbstractionAgent (Level 1)
    FULL_REANALYSIS = "full"  # Too many changes, start fresh


@dataclass
class ChangeImpact:
    """Result of analyzing the impact of changes."""

    # Categorized changes
    renames: dict[str, str] = field(default_factory=dict)  # old_path -> new_path
    modified_files: list[str] = field(default_factory=list)
    added_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)

    # Affected components
    dirty_components: set[str] = field(default_factory=set)

    # Components that need sub-analysis regeneration (expanded + structural changes)
    components_needing_reexpansion: set[str] = field(default_factory=set)

    # Cross-boundary analysis
    cross_boundary_changes: list[str] = field(default_factory=list)  # Files with cross-component refs

    # Escalation flags
    architecture_dirty: bool = False  # Level 1 needs refresh
    unassigned_files: list[str] = field(default_factory=list)  # New files without a component

    # Recommended action
    action: UpdateAction = UpdateAction.NONE
    reason: str = ""

    def summary(self) -> str:
        """Human-readable summary of the impact."""
        lines = [
            f"Action: {self.action.value}",
            f"Reason: {self.reason}",
            f"Renames: {len(self.renames)}",
            f"Modified: {len(self.modified_files)}",
            f"Added: {len(self.added_files)}",
            f"Deleted: {len(self.deleted_files)}",
            f"Dirty components: {self.dirty_components}",
        ]
        if self.components_needing_reexpansion:
            lines.append(f"🔄 Components needing re-expansion: {self.components_needing_reexpansion}")
        if self.architecture_dirty:
            lines.append("⚠️ Architecture refresh needed")
        if self.unassigned_files:
            lines.append(f"⚠️ Unassigned files: {self.unassigned_files}")
        return "\n".join(lines)


# Thresholds for escalation decisions
# These are intentionally high to prefer incremental updates over full reanalysis
STRUCTURAL_CHANGE_THRESHOLD = 0.30  # 30% of files added/deleted triggers full reanalysis
MAX_DIRTY_COMPONENTS_FOR_INCREMENTAL = 10  # More than this triggers architecture refresh
