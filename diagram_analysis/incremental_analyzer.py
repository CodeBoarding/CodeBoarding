"""
Incremental analysis for fast iterative updates.

This module determines the minimal work needed when code changes:
1. Classify changes (rename, modify, add, delete)
2. Map changes to affected components
3. Check if changes cross component boundaries
4. Execute minimal update plan (patch paths, update component, or full reanalysis)
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component
from diagram_analysis.manifest import AnalysisManifest, load_manifest, save_manifest, build_manifest_from_analysis
from repo_utils.change_detector import ChangeSet, ChangeType, detect_changes_from_commit
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import CallGraph

logger = logging.getLogger(__name__)


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
        if self.architecture_dirty:
            lines.append("⚠️ Architecture refresh needed")
        if self.unassigned_files:
            lines.append(f"⚠️ Unassigned files: {self.unassigned_files}")
        return "\n".join(lines)


# Thresholds for escalation decisions
STRUCTURAL_CHANGE_THRESHOLD = 0.05  # 5% of files added/deleted triggers full reanalysis
MAX_DIRTY_COMPONENTS_FOR_INCREMENTAL = 3  # More than this triggers architecture refresh


def analyze_impact(
    changes: ChangeSet,
    manifest: AnalysisManifest,
    static_analysis: StaticAnalysisResults | None = None,
) -> ChangeImpact:
    """
    Analyze the impact of changes and determine the update action.

    Args:
        changes: Detected file changes from git
        manifest: Previously saved analysis manifest
        static_analysis: Optional static analysis for cross-boundary detection

    Returns:
        ChangeImpact with categorized changes and recommended action
    """
    impact = ChangeImpact()

    if changes.is_empty():
        impact.action = UpdateAction.NONE
        impact.reason = "No changes detected"
        return impact

    # Categorize changes
    impact.renames = changes.renames
    impact.modified_files = changes.modified_files
    impact.added_files = changes.added_files
    impact.deleted_files = changes.deleted_files

    # Map changes to components
    _map_changes_to_components(impact, manifest)

    # Check for cross-boundary impact if we have static analysis
    if static_analysis:
        _check_cross_boundary_impact(impact, manifest, static_analysis)

    # Determine action
    _determine_action(impact, manifest)

    return impact


def _map_changes_to_components(impact: ChangeImpact, manifest: AnalysisManifest) -> None:
    """Map all changed files to their owning components."""

    # Process renames - use OLD path to find component
    for old_path, new_path in impact.renames.items():
        component = manifest.get_component_for_file(old_path)
        if component:
            impact.dirty_components.add(component)
        else:
            # Renamed file wasn't in any component - treat as structural
            impact.unassigned_files.append(new_path)

    # Process modifications
    for file_path in impact.modified_files:
        component = manifest.get_component_for_file(file_path)
        if component:
            impact.dirty_components.add(component)
        else:
            # Modified file not in manifest - might be new or previously untracked
            impact.unassigned_files.append(file_path)

    # Process additions
    for file_path in impact.added_files:
        # New files need component assignment
        impact.unassigned_files.append(file_path)

    # Process deletions
    for file_path in impact.deleted_files:
        component = manifest.get_component_for_file(file_path)
        if component:
            impact.dirty_components.add(component)


def _check_cross_boundary_impact(
    impact: ChangeImpact,
    manifest: AnalysisManifest,
    static_analysis: StaticAnalysisResults,
) -> None:
    """
    Check if modified files have references that cross component boundaries.

    Uses CFG edges to detect:
    - Functions in modified files that are called by other components
    - Functions in modified files that call into other components
    """
    for lang in static_analysis.get_languages():
        try:
            cfg = static_analysis.get_cfg(lang)
        except ValueError:
            continue

        for file_path in impact.modified_files:
            if _file_has_cross_boundary_refs(file_path, manifest, cfg):
                impact.cross_boundary_changes.append(file_path)
                impact.architecture_dirty = True


def _file_has_cross_boundary_refs(
    file_path: str,
    manifest: AnalysisManifest,
    cfg: CallGraph,
) -> bool:
    """
    Check if a file has CFG edges that cross component boundaries.

    Returns True if any edge connects to a file in a different component.
    """
    owning_component = manifest.get_component_for_file(file_path)
    if not owning_component:
        return False

    # Find all nodes in this file
    file_nodes = [node_name for node_name, node in cfg.nodes.items() if _path_matches(node.file_path, file_path)]

    if not file_nodes:
        return False

    # Check edges for cross-boundary connections
    for edge in cfg.edges:
        src_name = edge.get_source()
        dst_name = edge.get_destination()

        # Check if this edge involves our file
        if src_name in file_nodes or dst_name in file_nodes:
            # Get the other end of the edge
            other_name = dst_name if src_name in file_nodes else src_name
            other_node = cfg.nodes.get(other_name)

            if other_node:
                other_component = manifest.get_component_for_file(other_node.file_path)
                if other_component and other_component != owning_component:
                    logger.debug(
                        f"Cross-boundary edge detected: {file_path} ({owning_component}) "
                        f"<-> {other_node.file_path} ({other_component})"
                    )
                    return True

    return False


def _path_matches(absolute_path: str, relative_path: str) -> bool:
    """Check if an absolute path ends with the relative path."""
    # Normalize paths
    abs_normalized = absolute_path.replace("\\", "/")
    rel_normalized = relative_path.replace("\\", "/").lstrip("./")
    return abs_normalized.endswith(rel_normalized)


def _determine_action(impact: ChangeImpact, manifest: AnalysisManifest) -> None:
    """Determine the recommended update action based on impact analysis."""

    # No changes
    if not any([impact.renames, impact.modified_files, impact.added_files, impact.deleted_files]):
        impact.action = UpdateAction.NONE
        impact.reason = "No changes detected"
        return

    # Pure renames only
    if impact.renames and not any([impact.modified_files, impact.added_files, impact.deleted_files]):
        impact.action = UpdateAction.PATCH_PATHS
        impact.reason = f"Pure rename: {len(impact.renames)} file(s)"
        return

    # Check structural change threshold
    total_files = len(manifest.file_to_component)
    structural_count = len(impact.added_files) + len(impact.deleted_files)
    if total_files > 0 and structural_count / total_files > STRUCTURAL_CHANGE_THRESHOLD:
        impact.action = UpdateAction.FULL_REANALYSIS
        impact.reason = f"Structural changes exceed threshold: {structural_count}/{total_files} files"
        return

    # Too many dirty components
    if len(impact.dirty_components) > MAX_DIRTY_COMPONENTS_FOR_INCREMENTAL:
        impact.action = UpdateAction.UPDATE_ARCHITECTURE
        impact.reason = f"Too many affected components: {len(impact.dirty_components)}"
        return

    # Cross-boundary changes detected
    if impact.architecture_dirty:
        impact.action = UpdateAction.UPDATE_ARCHITECTURE
        impact.reason = f"Cross-boundary changes in: {impact.cross_boundary_changes}"
        return

    # Default: update only affected components
    if impact.dirty_components:
        impact.action = UpdateAction.UPDATE_COMPONENTS
        impact.reason = f"Update components: {impact.dirty_components}"
        return

    # Fallback
    impact.action = UpdateAction.FULL_REANALYSIS
    impact.reason = "Unable to determine minimal update path"


def patch_paths_in_analysis(
    analysis: AnalysisInsights,
    renames: dict[str, str],
) -> AnalysisInsights:
    """
    Patch file paths in analysis for renames (no LLM needed).

    Updates:
    - assigned_files in each Component
    - reference_file in key_entities

    Args:
        analysis: The analysis to patch
        renames: Mapping of old_path -> new_path

    Returns:
        Patched analysis (modified in place, also returned)
    """
    if not renames:
        return analysis

    logger.info(f"Patching {len(renames)} renamed paths in analysis")

    for component in analysis.components:
        # Patch assigned_files
        component.assigned_files = [renames.get(f, f) for f in component.assigned_files]

        # Patch key_entities reference_file
        for entity in component.key_entities:
            if entity.reference_file and entity.reference_file in renames:
                old_path = entity.reference_file
                entity.reference_file = renames[old_path]
                logger.debug(f"Patched key_entity path: {old_path} -> {entity.reference_file}")

    return analysis


def patch_paths_in_manifest(
    manifest: AnalysisManifest,
    renames: dict[str, str],
) -> AnalysisManifest:
    """
    Patch file paths in manifest for renames.

    Args:
        manifest: The manifest to patch
        renames: Mapping of old_path -> new_path

    Returns:
        Patched manifest (modified in place, also returned)
    """
    for old_path, new_path in renames.items():
        manifest.update_file_path(old_path, new_path)

    return manifest


def load_analysis(output_dir: Path) -> AnalysisInsights | None:
    """Load the main analysis.json file."""
    analysis_path = output_dir / "analysis.json"
    if not analysis_path.exists():
        return None

    try:
        with open(analysis_path, "r") as f:
            data = json.load(f)
        return AnalysisInsights.model_validate(data)
    except Exception as e:
        logger.error(f"Failed to load analysis: {e}")
        return None


def save_analysis(analysis: AnalysisInsights, output_dir: Path, expandable_components: list[str] | None = None) -> Path:
    """Save the analysis to analysis.json."""
    from diagram_analysis.analysis_json import from_analysis_to_json

    analysis_path = output_dir / "analysis.json"

    # Build list of Component objects for expandable check
    expandable = []
    if expandable_components:
        expandable = [c for c in analysis.components if c.name in expandable_components]

    with open(analysis_path, "w") as f:
        f.write(from_analysis_to_json(analysis, expandable))

    return analysis_path


class IncrementalUpdater:
    """
    Executes incremental updates based on impact analysis.

    Usage:
        updater = IncrementalUpdater(repo_dir, output_dir)
        result = updater.run()
    """

    def __init__(
        self,
        repo_dir: Path,
        output_dir: Path,
        static_analysis: StaticAnalysisResults | None = None,
        force_full: bool = False,
    ):
        self.repo_dir = repo_dir
        self.output_dir = output_dir
        self.static_analysis = static_analysis
        self.force_full = force_full

        self.manifest: AnalysisManifest | None = None
        self.analysis: AnalysisInsights | None = None
        self.impact: ChangeImpact | None = None

    def can_run_incremental(self) -> bool:
        """Check if incremental update is possible."""
        if self.force_full:
            return False

        self.manifest = load_manifest(self.output_dir)
        if not self.manifest:
            logger.info("No manifest found, full analysis required")
            return False

        self.analysis = load_analysis(self.output_dir)
        if not self.analysis:
            logger.info("No analysis found, full analysis required")
            return False

        return True

    def analyze(self) -> ChangeImpact:
        """Analyze the impact of changes since last analysis."""
        if not self.manifest:
            raise RuntimeError("Must call can_run_incremental() first")

        # Detect changes from the base commit
        changes = detect_changes_from_commit(self.repo_dir, self.manifest.base_commit)

        logger.info(
            f"Detected {len(changes.changes)} changes from {self.manifest.base_commit[:7]}: "
            f"{len(changes.renames)} renames, {len(changes.modified_files)} modified, "
            f"{len(changes.added_files)} added, {len(changes.deleted_files)} deleted"
        )

        # Analyze impact
        self.impact = analyze_impact(changes, self.manifest, self.static_analysis)

        logger.info(f"Impact analysis:\n{self.impact.summary()}")

        return self.impact

    def execute(self) -> bool:
        """
        Execute the update based on impact analysis.

        Returns True if update was successful, False if full reanalysis is needed.
        """
        if not self.impact or not self.manifest or not self.analysis:
            raise RuntimeError("Must call analyze() first")

        match self.impact.action:
            case UpdateAction.NONE:
                logger.info("No update needed")
                return True

            case UpdateAction.PATCH_PATHS:
                return self._execute_patch_paths()

            case UpdateAction.UPDATE_COMPONENTS:
                return self._execute_update_components()

            case UpdateAction.UPDATE_ARCHITECTURE:
                logger.info("Architecture update needed - falling back to full reanalysis")
                return False

            case UpdateAction.FULL_REANALYSIS:
                logger.info(f"Full reanalysis required: {self.impact.reason}")
                return False

        return False

    def _execute_patch_paths(self) -> bool:
        """Execute path patching for renames."""
        assert self.impact and self.manifest and self.analysis

        logger.info(f"Patching {len(self.impact.renames)} renamed paths")

        # Patch analysis
        patch_paths_in_analysis(self.analysis, self.impact.renames)

        # Patch manifest
        patch_paths_in_manifest(self.manifest, self.impact.renames)

        # Update manifest commit
        from repo_utils.change_detector import get_current_commit
        from repo_utils import get_repo_state_hash

        new_commit = get_current_commit(self.repo_dir) or self.manifest.base_commit
        self.manifest.base_commit = new_commit
        self.manifest.repo_state_hash = get_repo_state_hash(self.repo_dir)

        # Save updated files
        save_analysis(self.analysis, self.output_dir, self.manifest.expanded_components)
        save_manifest(self.manifest, self.output_dir)

        logger.info("Path patching complete")
        return True

    def _execute_update_components(self) -> bool:
        """Execute targeted component updates."""
        assert self.impact and self.manifest and self.analysis

        # TODO: Implement targeted component re-analysis
        # This will call DetailsAgent.run() for each dirty component
        # For now, fall back to full reanalysis

        logger.info(f"Component update for {self.impact.dirty_components} - not yet implemented")
        return False
