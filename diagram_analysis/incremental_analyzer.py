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
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import cast

from tqdm import tqdm

from agents.agent_responses import AnalysisInsights, Component, MetaAnalysisInsights
from agents.validation import ValidationContext, validate_component_relationships, validate_key_entities
from diagram_analysis.manifest import AnalysisManifest, load_manifest, save_manifest, build_manifest_from_analysis
from repo_utils.change_detector import ChangeSet, ChangeType, DetectedChange, detect_changes_from_commit
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import CallGraph, ClusterResult

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

    # Categorize changes - FILTER out non-source files upfront
    # This ensures threshold calculations only consider relevant files
    impact.renames = {old: new for old, new in changes.renames.items() if not _should_skip_file(new)}
    impact.modified_files = [f for f in changes.modified_files if not _should_skip_file(f)]
    impact.added_files = [f for f in changes.added_files if not _should_skip_file(f)]
    impact.deleted_files = [f for f in changes.deleted_files if not _should_skip_file(f)]

    # Map changes to components
    _map_changes_to_components(impact, manifest)

    # Check for cross-boundary impact if we have static analysis
    if static_analysis:
        _check_cross_boundary_impact(impact, manifest, static_analysis)

    # Determine action
    _determine_action(impact, manifest)

    return impact


def _filter_changes_for_scope(changes: ChangeSet, scope_files: set[str]) -> ChangeSet:
    """Filter a ChangeSet down to the files that belong to a scope.

    A change is included when either the path itself is part of the scope or it
    lives in the same directory as a scoped file. This keeps added files (which
    are not yet in the manifest) visible when they land alongside existing
    scoped files.
    """

    if changes.is_empty() or not scope_files:
        return ChangeSet()

    scope_dirs = {str(Path(f).parent) for f in scope_files}

    def in_scope(path: str) -> bool:
        path_dir = str(Path(path).parent)
        return path in scope_files or path_dir in scope_dirs

    scoped_changes: list[DetectedChange] = []

    for change in changes.changes:
        if change.change_type == ChangeType.RENAMED:
            old_path = change.old_path or ""
            if in_scope(change.file_path) or in_scope(old_path):
                scoped_changes.append(change)
        else:
            if in_scope(change.file_path):
                scoped_changes.append(change)

    return ChangeSet(scoped_changes)


def _detect_expanded_components_for_analysis(analysis: AnalysisInsights, output_dir: Path) -> list[str]:
    """Find components that already have sub-analysis JSONs on disk."""
    from output_generators.markdown import sanitize

    expanded: list[str] = []
    for component in analysis.components:
        safe_name = sanitize(component.name)
        if (output_dir / f"{safe_name}.json").exists():
            expanded.append(component.name)
    return expanded


def _should_skip_file(file_path: str) -> bool:
    """Check if a file should be skipped (not part of core source analysis)."""
    skip_patterns = [
        "tests/",
        "test_",
        "__pycache__/",
        ".pytest_cache/",
        "README",
        "CHANGELOG",
        "LICENSE",
        "CONTRIBUTING",
        "AGENTS.md",
        "CLAUDE.md",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "uv.lock",
        "poetry.lock",
        "Pipfile",
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
        "Dockerfile",
        "docker-compose",
        ".dockerignore",
        "Makefile",
        "justfile",
    ]
    # Skip files matching patterns
    if any(pattern in file_path for pattern in skip_patterns):
        return True
    # Skip non-source file extensions
    skip_extensions = [".md", ".txt", ".rst", ".yml", ".yaml", ".json", ".toml", ".lock"]
    if any(file_path.endswith(ext) for ext in skip_extensions):
        return True
    return False


def _map_changes_to_components(impact: ChangeImpact, manifest: AnalysisManifest) -> None:
    """Map all changed files to their owning components.

    Note: Files are already filtered by _should_skip_file() in analyze_impact().

    Also tracks which expanded components need re-expansion due to structural changes.
    """
    # Track components with structural changes (added/deleted/modified files)
    # Modified files in expanded components need re-expansion to get fresh static analysis
    components_with_structural_changes: set[str] = set()

    # Process renames - use OLD path to find component
    for old_path, new_path in impact.renames.items():
        component = manifest.get_component_for_file(old_path)
        if component:
            impact.dirty_components.add(component)
        else:
            # Renamed file wasn't in any component - treat as unassigned
            impact.unassigned_files.append(new_path)

    # Process modifications - these require re-expansion for expanded components
    # because code changes may affect static analysis (call graphs, dependencies, etc.)
    for file_path in impact.modified_files:
        component = manifest.get_component_for_file(file_path)
        if component:
            impact.dirty_components.add(component)
            components_with_structural_changes.add(component)
        # Modified files not in manifest are silently ignored (never tracked)

    # Process additions - new source files need component assignment
    # If they go to an expanded component, that component needs re-expansion
    for file_path in impact.added_files:
        impact.unassigned_files.append(file_path)
        # We'll determine the target component later in _assign_new_files

    # Process deletions - structural change, may need re-expansion
    for file_path in impact.deleted_files:
        component = manifest.get_component_for_file(file_path)
        if component:
            impact.dirty_components.add(component)
            components_with_structural_changes.add(component)

    # Track components with structural changes for potential re-expansion
    # The actual decision of whether to re-expand is made at execution time
    # when we can check if the component has a sub-analysis file
    impact.components_needing_reexpansion = components_with_structural_changes.copy()


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


def load_sub_analysis(output_dir: Path, component_name: str) -> AnalysisInsights | None:
    """Load a sub-analysis JSON file for a component."""
    from output_generators.markdown import sanitize

    safe_name = sanitize(component_name)
    sub_analysis_path = output_dir / f"{safe_name}.json"

    if not sub_analysis_path.exists():
        return None

    try:
        with open(sub_analysis_path, "r") as f:
            data = json.load(f)
        return AnalysisInsights.model_validate(data)
    except Exception as e:
        logger.error(f"Failed to load sub-analysis for {component_name}: {e}")
        return None


def save_sub_analysis(
    sub_analysis: AnalysisInsights,
    output_dir: Path,
    component_name: str,
    expandable_components: list[str] | None = None,
) -> Path:
    """Save a sub-analysis JSON file for a component."""
    from diagram_analysis.analysis_json import from_analysis_to_json
    from output_generators.markdown import sanitize

    safe_name = sanitize(component_name)
    sub_analysis_path = output_dir / f"{safe_name}.json"

    expandable = []
    if expandable_components:
        expandable = [c for c in sub_analysis.components if c.name in expandable_components]

    with open(sub_analysis_path, "w") as f:
        f.write(from_analysis_to_json(sub_analysis, expandable))

    return sub_analysis_path


def patch_sub_analysis(
    sub_analysis: AnalysisInsights,
    deleted_files: list[str],
    renames: dict[str, str],
) -> bool:
    """
    Patch a sub-analysis by removing deleted files and applying renames.

    Returns True if any changes were made.
    """
    changed = False

    # Build a set of deleted file patterns (handle both with and without repo prefix)
    deleted_patterns: set[str] = set()
    for f in deleted_files:
        deleted_patterns.add(f)
        # Also add normalized versions
        if f.startswith("repos/"):
            # Strip "repos/RepoName/" prefix
            parts = f.split("/", 2)
            if len(parts) > 2:
                deleted_patterns.add(parts[2])
        else:
            deleted_patterns.add(f.lstrip("./"))

    # Build rename patterns (handle both with and without repo prefix)
    rename_map: dict[str, str] = {}
    for old, new in renames.items():
        rename_map[old] = new
        rename_map[old.lstrip("./")] = new
        if old.startswith("repos/"):
            parts = old.split("/", 2)
            if len(parts) > 2:
                rename_map[parts[2]] = new

    def file_is_deleted(path: str) -> bool:
        normalized = path.lstrip("./")
        if normalized in deleted_patterns or path in deleted_patterns:
            return True
        # Check if it ends with any deleted file
        for pattern in deleted_patterns:
            if path.endswith(pattern) or normalized.endswith(pattern):
                return True
        return False

    def get_renamed_path(path: str) -> str | None:
        normalized = path.lstrip("./")
        if normalized in rename_map:
            return rename_map[normalized]
        if path in rename_map:
            return rename_map[path]
        for old, new in rename_map.items():
            if path.endswith(old) or normalized.endswith(old):
                return new
        return None

    for component in sub_analysis.components:
        # Remove deleted files from assigned_files
        orig_len = len(component.assigned_files)
        component.assigned_files = [f for f in component.assigned_files if not file_is_deleted(f)]
        if len(component.assigned_files) < orig_len:
            changed = True

        # Apply renames to assigned_files
        new_assigned = []
        for f in component.assigned_files:
            new_path = get_renamed_path(f)
            if new_path:
                new_assigned.append(new_path)
                changed = True
            else:
                new_assigned.append(f)
        component.assigned_files = new_assigned

        # Remove key_entities referencing deleted files
        orig_entities = len(component.key_entities)
        component.key_entities = [
            e for e in component.key_entities if not (e.reference_file and file_is_deleted(e.reference_file))
        ]
        if len(component.key_entities) < orig_entities:
            changed = True

        # Apply renames to key_entities
        for entity in component.key_entities:
            if entity.reference_file:
                new_path = get_renamed_path(entity.reference_file)
                if new_path:
                    entity.reference_file = new_path
                    changed = True

    return changed


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
        self.changes: ChangeSet | None = None
        self.component_impacts: dict[str, ChangeImpact] = {}

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
        self.changes = changes

        logger.info(
            f"Detected {len(changes.changes)} changes from {self.manifest.base_commit[:7]}: "
            f"{len(changes.renames)} renames, {len(changes.modified_files)} modified, "
            f"{len(changes.added_files)} added, {len(changes.deleted_files)} deleted"
        )

        # Analyze impact
        self.impact = analyze_impact(changes, self.manifest, self.static_analysis)

        logger.info(f"Impact analysis:\n{self.impact.summary()}")

        # Also analyze each expanded component in scope so we can recurse if needed
        self.component_impacts = self._analyze_expanded_component_impacts(changes)

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

    def recompute_dirty_components(self, static_analysis: StaticAnalysisResults) -> None:
        """
        Recompute which components are actually affected after static analysis.

        This uses the updated cluster results from static analysis to determine
        which components have files that actually changed, rather than relying
        on the manifest's old file assignments.

        Args:
            static_analysis: Updated static analysis with new cluster assignments
        """
        if not self.impact or not self.manifest or not self.analysis:
            logger.warning("Cannot recompute dirty components: missing impact, manifest, or analysis")
            return

        logger.info("Recomputing dirty components with updated cluster assignments...")

        from static_analyzer.cluster_helpers import build_all_cluster_results

        # Build cluster results from updated static analysis
        cluster_results = build_all_cluster_results(static_analysis)

        # Get the changed files
        changed_files: set[str] = set()
        changed_files.update(self.impact.renames.keys())
        changed_files.update(self.impact.renames.values())
        changed_files.update(self.impact.modified_files)
        changed_files.update(self.impact.added_files)
        changed_files.update(self.impact.deleted_files)

        # For each changed file, find which component it belongs to in the NEW analysis
        new_dirty_components: set[str] = set()

        # Keep track of components inferred from the existing manifest as a fallback
        # (especially important for deleted files that won't appear in new clusters)
        manifest_dirty_components: set[str] = set()

        for file_path in changed_files:
            # Skip non-source files
            if _should_skip_file(file_path):
                continue

            # Find which component this file should belong to based on cluster membership
            target_component = self._find_component_for_file(file_path, cluster_results)

            if target_component:
                new_dirty_components.add(target_component)
                logger.debug(f"File '{file_path}' assigned to component '{target_component}'")
            else:
                # File doesn't belong to any component's clusters
                logger.debug(f"File '{file_path}' not assigned to any component")

            # Fallback: use manifest mapping when cluster-based mapping fails
            if self.manifest:
                manifest_component = self.manifest.get_component_for_file(file_path)
                if manifest_component:
                    manifest_dirty_components.add(manifest_component)
                    logger.debug(
                        "File '%s' falls back to manifest component '%s'",
                        file_path,
                        manifest_component,
                    )

        # Update the impact with the recomputed dirty components
        original_dirty = self.impact.dirty_components.copy()
        self.impact.dirty_components = new_dirty_components | manifest_dirty_components

        # Also update components_needing_reexpansion. Preserve structural-change components
        # detected earlier (deleted/added files) even if clusters couldn't be mapped.
        structural_components = set(self.impact.components_needing_reexpansion)
        if self.manifest:
            for file_path in self.impact.added_files + self.impact.deleted_files:
                comp = self.manifest.get_component_for_file(file_path)
                if comp:
                    structural_components.add(comp)

        self.impact.components_needing_reexpansion = structural_components & self.impact.dirty_components

        logger.info(
            f"Recomputed dirty components: {len(original_dirty)} -> {len(new_dirty_components)} "
            f"(removed: {original_dirty - new_dirty_components}, added: {new_dirty_components - original_dirty})"
        )

    def _find_component_for_file(self, file_path: str, cluster_results: dict) -> str | None:
        """
        Find which component a file belongs to based on cluster membership.

        Args:
            file_path: Path to the file
            cluster_results: Cluster results from static analysis

        Returns:
            Component name if found, None otherwise
        """
        # Get the clusters this file belongs to
        file_clusters: set[int] = set()
        for lang_result in cluster_results.values():
            file_clusters.update(lang_result.get_clusters_for_file(file_path))

        if not file_clusters:
            return None

        # Find which component has the most overlap with these clusters
        best_component = None
        best_overlap = 0

        assert self.analysis is not None, "Analysis must be loaded"

        for component in self.analysis.components:
            if not component.source_cluster_ids:
                continue

            component_clusters = set(component.source_cluster_ids)
            overlap = len(file_clusters & component_clusters)

            if overlap > best_overlap:
                best_overlap = overlap
                best_component = component.name

        # If no cluster overlap, try directory-based matching
        if best_component is None:
            file_dir = str(Path(file_path).parent)
            for component in self.analysis.components:
                for assigned_file in component.assigned_files:
                    if str(Path(assigned_file).parent) == file_dir:
                        return component.name

        return best_component

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
        """
        Execute targeted component updates.

        For most changes (file modifications within a component), we just update
        the assigned_files list without re-running LLM analysis. The component's
        description and structure don't change just because code was modified.

        LLM re-analysis (via DetailsAgent) is needed when:
        - Expanded component has files added or deleted (structural change)
        """
        assert self.impact and self.manifest and self.analysis

        logger.info(f"Updating {len(self.impact.dirty_components)} components: {self.impact.dirty_components}")

        from output_generators.markdown import sanitize

        # Step 1: Handle deleted files first (before assigning new ones)
        if self.impact.deleted_files:
            self._remove_deleted_files(self.impact.deleted_files)

        # Step 2: Assign new files to components and track which ones got new files
        components_with_new_files: set[str] = set()
        if self.impact.added_files:
            components_with_new_files = self._assign_new_files(self.impact.added_files)

        # Step 3: Apply renames
        if self.impact.renames:
            patch_paths_in_analysis(self.analysis, self.impact.renames)
            patch_paths_in_manifest(self.manifest, self.impact.renames)

        # Step 4: Determine which expanded components need re-expansion
        #
        # An expanded component is one that has a sub-analysis JSON file.
        # We check file existence for backward compatibility with manifests
        # that have incorrect expanded_components data.
        #
        # IMPORTANT: We only re-expand if the component's LOGICAL STRUCTURE changed,
        # not just file assignments. If files were added/deleted/renamed but the
        # component's description, relationships, and sub-components are unchanged,
        # we patch file references instead of re-running LLM analysis.
        components_to_reexpand: set[str] = set()
        components_to_patch: set[str] = set()

        # Check all components that might need updates
        components_to_check = self.impact.components_needing_reexpansion | components_with_new_files

        # Track which components need targeted classification vs simple patching
        components_to_classify: dict[str, list[str]] = {}  # component_name -> new_files

        for component_name in components_to_check:
            if not self._is_expanded_component(component_name):
                continue

            # Check if changes are just file renames/reassignments
            # If so, we can patch instead of re-expanding
            if self._component_has_only_renames(component_name):
                logger.info(f"Component '{component_name}' has only renames, will patch instead of re-expanding")
                components_to_patch.add(component_name)
            else:
                # Component has true structural changes
                # Check if the sub-analysis can be patched or needs regeneration
                if self._can_patch_sub_analysis(component_name):
                    logger.info(f"Component '{component_name}' can be patched without LLM re-analysis")
                    components_to_patch.add(component_name)
                    # If this component also has new files, we need targeted classification
                    if component_name in components_with_new_files:
                        # Get new files that were assigned to this component
                        new_files_for_component = self._get_new_files_for_component(
                            component_name, self.impact.added_files if self.impact else []
                        )
                        if new_files_for_component:
                            components_to_classify[component_name] = new_files_for_component
                else:
                    logger.info(f"Component '{component_name}' needs full re-expansion")
                    components_to_reexpand.add(component_name)

        # Step 5: Re-run DetailsAgent for components that need re-expansion
        reexpanded_components: list[str] = []
        if components_to_reexpand:
            reexpanded_components = self._reexpand_components(components_to_reexpand)

        # Step 5b: Run scoped impact summaries for changed expanded components
        self._run_scoped_component_impacts(components_to_reexpand | components_to_patch)

        # Step 6: Patch components that don't need full re-expansion
        # These are components where only file assignments changed, not logical structure
        patched_components: list[str] = []
        classified_components: list[str] = []

        deleted_files = self.impact.deleted_files
        renames = self.impact.renames

        # First, run targeted classification for components with new files
        for component_name, new_files in components_to_classify.items():
            if self._classify_new_files_in_component(component_name, new_files):
                classified_components.append(component_name)
                logger.info(f"Component '{component_name}' new files classified into sub-components")

        # Then patch remaining components
        for component_name in components_to_patch:
            component = next(
                (c for c in self.analysis.components if c.name == component_name),
                None,
            )
            if not component:
                logger.warning(f"Component '{component_name}' not found in analysis")
                continue

            safe_name = sanitize(component_name)
            sub_analysis_path = self.output_dir / f"{safe_name}.json"

            if sub_analysis_path.exists():
                sub_analysis = load_sub_analysis(self.output_dir, component_name)
                if sub_analysis:
                    if patch_sub_analysis(sub_analysis, deleted_files, renames):
                        save_sub_analysis(sub_analysis, self.output_dir, component_name)
                        logger.info(f"Component '{component_name}' sub-analysis patched")
                patched_components.append(component_name)
            else:
                logger.info(f"Component '{component_name}' has no sub-analysis file, updating in place")
                patched_components.append(component_name)

        # Step 7: Validate the updated analysis
        if self.static_analysis:
            is_valid = self._validate_incremental_update(self.analysis, self.static_analysis)
            if not is_valid:
                logger.warning(
                    "Incremental update validation failed - analysis may have inconsistencies. "
                    "Consider re-running full analysis for complete results."
                )
        else:
            logger.warning("No static analysis available for validation")

        # Step 8: Update manifest with new commit
        from repo_utils.change_detector import get_current_commit
        from repo_utils import get_repo_state_hash

        new_commit = get_current_commit(self.repo_dir) or self.manifest.base_commit
        self.manifest.base_commit = new_commit
        self.manifest.repo_state_hash = get_repo_state_hash(self.repo_dir)

        # Step 9: Save updated files
        save_analysis(self.analysis, self.output_dir, self.manifest.expanded_components)
        save_manifest(self.manifest, self.output_dir)

        logger.info(
            f"Component update complete. "
            f"Re-expanded: {reexpanded_components}, Classified: {classified_components}, Patched: {patched_components}"
        )
        return True

    def _is_expanded_component(self, component_name: str) -> bool:
        """Check if a component has a sub-analysis file (is expanded).

        This checks both the manifest AND file existence for backward compatibility
        with manifests that have incorrect expanded_components data.
        """
        from output_generators.markdown import sanitize

        # Check manifest first
        if self.manifest and component_name in self.manifest.expanded_components:
            return True

        # Fallback: check if sub-analysis file exists
        safe_name = sanitize(component_name)
        sub_analysis_path = self.output_dir / f"{safe_name}.json"
        return sub_analysis_path.exists()

    def _component_has_only_renames(self, component_name: str) -> bool:
        """Check if a component's structural changes are just file renames.

        A component has "only renames" if:
        1. All deleted files are old paths of renamed files
        2. All modified files are the new paths of renamed files
        3. No true additions or deletions

        Args:
            component_name: Name of the component to check

        Returns:
            True if the component's changes are just renames that can be patched
        """
        if not self.impact or not self.manifest:
            return False

        # Get all files associated with this component
        component_files = set()
        for file_path, comp in self.manifest.file_to_component.items():
            if comp == component_name:
                component_files.add(file_path)

        # Check if all "deleted" files are actually old paths of renames
        deleted_in_component = set()
        for file_path in self.impact.deleted_files:
            if file_path in component_files:
                deleted_in_component.add(file_path)

        # Check if all "modified" files are actually new paths of renames
        modified_in_component = set()
        for file_path in self.impact.modified_files:
            if file_path in component_files:
                modified_in_component.add(file_path)

        # Get renames that affect this component
        renames_in_component = {}
        for old_path, new_path in self.impact.renames.items():
            if old_path in component_files or new_path in component_files:
                renames_in_component[old_path] = new_path

        # Log detailed analysis for debugging
        logger.debug(
            f"Component '{component_name}' change analysis: "
            f"deleted={deleted_in_component}, modified={modified_in_component}, "
            f"renames={renames_in_component}"
        )

        # If no structural changes at all, it's not "only renames" (it's nothing)
        if not deleted_in_component and not modified_in_component:
            return False

        # Check if all deletions are just old paths of renames
        deleted_are_all_renames = deleted_in_component.issubset(set(renames_in_component.keys()))

        # Check if all modifications are just new paths of renames
        modified_are_all_renames = modified_in_component.issubset(set(renames_in_component.values()))

        # Log the decision
        if deleted_are_all_renames and modified_are_all_renames:
            logger.debug(f"Component '{component_name}' has only renames")
        else:
            logger.debug(
                f"Component '{component_name}' has true structural changes: "
                f"deleted_are_renames={deleted_are_all_renames}, "
                f"modified_are_renames={modified_are_all_renames}"
            )

        # If both conditions are true, this component only has renames
        return deleted_are_all_renames and modified_are_all_renames

    def _can_patch_sub_analysis(self, component_name: str) -> bool:
        """Check if a component's sub-analysis can be patched without LLM re-analysis.

        This determines whether we can update file references in the existing
        sub-analysis instead of regenerating it with DetailsAgent.

        A sub-analysis can be patched if:
        1. The component still exists in the current analysis
        2. The sub-analysis file exists
        3. Changes are limited to file assignments (added/deleted/renamed files)
           without changing the component's logical structure

        Args:
            component_name: Name of the component to check

        Returns:
            True if the sub-analysis can be patched, False if it needs regeneration
        """
        if not self.analysis or not self.manifest:
            return False

        # Check component exists
        component = next(
            (c for c in self.analysis.components if c.name == component_name),
            None,
        )
        if not component:
            return False

        # Check sub-analysis file exists
        from output_generators.markdown import sanitize

        safe_name = sanitize(component_name)
        sub_analysis_path = self.output_dir / f"{safe_name}.json"
        if not sub_analysis_path.exists():
            return False

        # Load existing sub-analysis
        sub_analysis = load_sub_analysis(self.output_dir, component_name)
        if not sub_analysis:
            return False

        # Get all files in the sub-analysis
        subcomponent_files: set[str] = set()
        for sub_component in sub_analysis.components:
            subcomponent_files.update(sub_component.assigned_files)

        # Check what changes affect this component's sub-analysis
        has_additions = False
        has_deletions = False
        has_renames = False

        # Check for added files in sub-components
        for file_path in self.impact.added_files if self.impact else []:
            if file_path in subcomponent_files:
                has_additions = True
                break

        # Check for deleted files in sub-components
        for file_path in self.impact.deleted_files if self.impact else []:
            if file_path in subcomponent_files:
                has_deletions = True
                break

        # Check for renames in sub-components
        for old_path, new_path in self.impact.renames.items() if self.impact else {}:
            if old_path in subcomponent_files or new_path in subcomponent_files:
                has_renames = True
                break

        # We can patch if there are file changes but no structural logic changes
        # For additions, we'll handle them via targeted classification rather than full re-expansion
        logger.debug(
            f"Component '{component_name}' sub-analysis check: "
            f"additions={has_additions}, deletions={has_deletions}, renames={has_renames}"
        )

        # Can patch if:
        # - Only renames: just patch paths
        # - Additions: we'll run targeted classification (handled separately)
        # - Deletions: need to check if it affects structure
        if has_deletions:
            # Deletions might affect component structure, need re-expansion
            logger.info(f"Component '{component_name}' has deletions, needs re-expansion")
            return False

        return True

    def _subcomponent_has_only_renames(self, component_name: str, sub_analysis: AnalysisInsights) -> bool:
        """Check if changes within a component's sub-analysis are just renames.

        This validates whether we can patch the sub-analysis instead of re-running
        the DetailsAgent. Similar to _component_has_only_renames but operates at
        the sub-component level.

        A sub-analysis has "only renames" if:
        1. All deleted files in sub-components are old paths of renamed files
        2. All modified files in sub-components are new paths of renamed files
        3. No true structural changes (additions/deletions) in sub-components

        Args:
            component_name: Name of the parent component
            sub_analysis: The sub-analysis to check

        Returns:
            True if the sub-analysis changes are just renames that can be patched
        """
        if not self.impact:
            return False

        # Collect all files from sub-components
        subcomponent_files: set[str] = set()
        for sub_component in sub_analysis.components:
            subcomponent_files.update(sub_component.assigned_files)

        # Check deleted files in sub-components
        deleted_in_subcomponent = set()
        for file_path in self.impact.deleted_files:
            if file_path in subcomponent_files:
                deleted_in_subcomponent.add(file_path)

        # Check modified files in sub-components
        modified_in_subcomponent = set()
        for file_path in self.impact.modified_files:
            if file_path in subcomponent_files:
                modified_in_subcomponent.add(file_path)

        # Get renames that affect sub-components
        renames_in_subcomponent = {}
        for old_path, new_path in self.impact.renames.items():
            if old_path in subcomponent_files or new_path in subcomponent_files:
                renames_in_subcomponent[old_path] = new_path

        # Log detailed analysis
        logger.debug(
            f"Sub-component analysis for '{component_name}': "
            f"deleted={deleted_in_subcomponent}, modified={modified_in_subcomponent}, "
            f"renames={renames_in_subcomponent}"
        )

        # If no structural changes in sub-components, nothing to patch
        if not deleted_in_subcomponent and not modified_in_subcomponent:
            return False

        # Check if all deletions are just old paths of renames
        deleted_are_all_renames = deleted_in_subcomponent.issubset(set(renames_in_subcomponent.keys()))

        # Check if all modifications are just new paths of renames
        modified_are_all_renames = modified_in_subcomponent.issubset(set(renames_in_subcomponent.values()))

        # Log the decision
        if deleted_are_all_renames and modified_are_all_renames:
            logger.debug(f"Sub-analysis for '{component_name}' has only renames, can be patched")
        else:
            logger.debug(
                f"Sub-analysis for '{component_name}' has true structural changes: "
                f"deleted_are_renames={deleted_are_all_renames}, "
                f"modified_are_renames={modified_are_all_renames}"
            )

        return deleted_are_all_renames and modified_are_all_renames

    def _reexpand_single_component(
        self,
        component_name: str,
        details_agent,
        plan_analysis,
        from_analysis_to_json,
        sanitize,
    ) -> str | None:
        """
        Process a single component for re-expansion.

        First checks if the existing sub-analysis can be patched instead of
        regenerated (if changes are just renames/reassigns within this component).

        Returns the component name if successful, None otherwise.
        """
        # Find the component in analysis (self.analysis is guaranteed to exist by caller)
        assert self.analysis is not None
        component = next(
            (c for c in self.analysis.components if c.name == component_name),
            None,
        )
        if not component:
            logger.warning(f"Component '{component_name}' not found for re-expansion")
            return None

        try:
            # Check if we can patch the existing sub-analysis instead of re-running
            safe_name = sanitize(component_name)
            sub_analysis_path = self.output_dir / f"{safe_name}.json"

            if sub_analysis_path.exists():
                existing_sub_analysis = load_sub_analysis(self.output_dir, component_name)
                if existing_sub_analysis:
                    # Check if changes within this component are just renames
                    if self._subcomponent_has_only_renames(component_name, existing_sub_analysis):
                        logger.info(
                            f"Component '{component_name}' sub-analysis has only renames, patching instead of re-expanding"
                        )

                        # Patch the sub-analysis
                        if patch_sub_analysis(
                            existing_sub_analysis,
                            self.impact.deleted_files if self.impact else [],
                            self.impact.renames if self.impact else {},
                        ):
                            # Get expandable sub-components from the patched analysis
                            new_components = plan_analysis(
                                existing_sub_analysis, parent_had_clusters=bool(component.source_cluster_ids)
                            )

                            # Save patched sub-analysis
                            with open(sub_analysis_path, "w") as f:
                                f.write(from_analysis_to_json(existing_sub_analysis, new_components))

                            logger.info(f"Patched component '{component_name}' sub-analysis -> {sub_analysis_path}")
                            return component_name

            # If patching wasn't possible or changes are structural, re-run DetailsAgent
            logger.info(f"Re-expanding component: {component_name}")

            # Run DetailsAgent to regenerate sub-analysis
            sub_analysis, _ = details_agent.run(component)

            # Get expandable sub-components
            new_components = plan_analysis(sub_analysis, parent_had_clusters=bool(component.source_cluster_ids))

            # Save sub-analysis
            with open(sub_analysis_path, "w") as f:
                f.write(from_analysis_to_json(sub_analysis, new_components))

            logger.info(f"Re-expanded component '{component_name}' -> {sub_analysis_path}")
            return component_name

        except Exception as e:
            logger.error(f"Failed to re-expand component '{component_name}': {e}")
            return None

    def _reexpand_components(self, component_names: set[str]) -> list[str]:
        """
        Re-run DetailsAgent for components that need sub-analysis regeneration.

        This is called when files are added/deleted from an expanded component,
        requiring the sub-analysis to be regenerated.

        Components are processed in parallel using ThreadPoolExecutor for efficiency.

        Returns list of successfully re-expanded component names.
        """
        assert self.analysis and self.manifest

        if not component_names:
            return []

        logger.info(f"Re-expanding {len(component_names)} components: {component_names}")

        # Use existing static analysis - don't reload!
        # self.static_analysis was already populated during the initial incremental analysis
        from agents.details_agent import DetailsAgent
        from agents.meta_agent import MetaAgent
        from agents.planner_agent import plan_analysis
        from diagram_analysis.analysis_json import from_analysis_to_json
        from output_generators.markdown import sanitize

        if not self.static_analysis:
            logger.error("No static analysis available for re-expansion")
            return []

        # Initialize agents using existing static analysis
        meta_agent = MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.repo_dir.name,
            static_analysis=self.static_analysis,
        )
        meta_context = cast(MetaAnalysisInsights, meta_agent.analyze_project_metadata())

        details_agent = DetailsAgent(
            repo_dir=self.repo_dir,
            project_name=self.repo_dir.name,
            static_analysis=self.static_analysis,
            meta_context=meta_context,
        )

        reexpanded: list[str] = []
        max_workers = min(os.cpu_count() or 4, 8)  # Limit to 8 workers max

        # Process components in parallel using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all component processing tasks
            future_to_component = {
                executor.submit(
                    self._reexpand_single_component,
                    component_name,
                    details_agent,
                    plan_analysis,
                    from_analysis_to_json,
                    sanitize,
                ): component_name
                for component_name in component_names
            }

            # Collect results as they complete
            for future in tqdm(
                as_completed(future_to_component),
                total=len(future_to_component),
                desc="Re-expanding components",
            ):
                component_name = future_to_component[future]
                try:
                    result = future.result()
                    if result:
                        reexpanded.append(result)
                except Exception as exc:
                    logger.error(f"Component {component_name} generated an exception: {exc}")

        logger.info(f"Successfully re-expanded {len(reexpanded)}/{len(component_names)} components")
        return reexpanded

    def _validate_incremental_update(self, analysis: AnalysisInsights, static_analysis: StaticAnalysisResults) -> bool:
        """
        Validate the updated analysis after incremental changes.

        Runs validation checks to ensure component relationships and key entities
        are still valid after the incremental update.

        Args:
            analysis: The updated analysis to validate
            static_analysis: Static analysis results for building validation context

        Returns:
            True if validation passes, False otherwise
        """
        from static_analyzer.cluster_helpers import build_all_cluster_results

        logger.info("Running incremental update validation...")

        # Build cluster results from static analysis
        cluster_results = build_all_cluster_results(static_analysis)

        # Build validation context
        context = ValidationContext(
            cluster_results=cluster_results,
            cfg_graphs={lang: static_analysis.get_cfg(lang) for lang in static_analysis.get_languages()},
        )

        # Run validators
        validators = [validate_component_relationships, validate_key_entities]
        all_valid = True

        for validator in validators:
            try:
                result = validator(analysis, context)
                if not result.is_valid:
                    all_valid = False
                    logger.warning(f"[Incremental Validation] {validator.__name__} failed: {result.feedback_messages}")
                else:
                    logger.info(f"[Incremental Validation] {validator.__name__} passed")
            except Exception as e:
                logger.error(f"[Incremental Validation] {validator.__name__} raised exception: {e}")
                all_valid = False

        if all_valid:
            logger.info("[Incremental Validation] All validation checks passed")
        else:
            logger.warning("[Incremental Validation] Some validation checks failed - consider re-running full analysis")

        return all_valid

    def _assign_new_files(self, new_files: list[str]) -> set[str]:
        """Assign new files to components based on directory heuristics.

        Returns set of component names that received new files.
        """
        assert self.analysis and self.manifest

        assigned_count = 0
        skipped_count = 0
        components_with_new_files: set[str] = set()

        for file_path in new_files:
            # Skip non-source files (uses same filter as _map_changes_to_components)
            if _should_skip_file(file_path):
                logger.debug(f"Skipping non-source file: {file_path}")
                skipped_count += 1
                continue

            # Try to find a component whose files share the same directory
            file_dir = str(Path(file_path).parent)

            best_component = None
            best_match_count = 0

            for component in self.analysis.components:
                # Count files in the same directory
                match_count = sum(1 for f in component.assigned_files if str(Path(f).parent) == file_dir)
                if match_count > best_match_count:
                    best_match_count = match_count
                    best_component = component

            if best_component:
                best_component.assigned_files.append(file_path)
                self.manifest.add_file(file_path, best_component.name)
                assigned_count += 1
                components_with_new_files.add(best_component.name)
                logger.debug(f"Assigned new file '{file_path}' to component '{best_component.name}'")
            else:
                logger.debug(f"Could not assign new file '{file_path}' to any component")

        logger.info(f"File assignment: {assigned_count} assigned, {skipped_count} skipped (non-source)")
        return components_with_new_files

    def _analyze_expanded_component_impacts(self, changes: ChangeSet) -> dict[str, ChangeImpact]:
        """Run analyze_impact within each expanded component's scope.

        This lets us reuse the same impact logic recursively for sub-analyses by
        filtering the ChangeSet to the files that belong to a component.
        """

        if not self.manifest:
            return {}

        component_impacts: dict[str, ChangeImpact] = {}

        for component_name in self.manifest.expanded_components:
            # Collect the files currently assigned to this component
            component_files = {f for f, comp in self.manifest.file_to_component.items() if comp == component_name}

            if not component_files:
                continue

            scoped_changes = _filter_changes_for_scope(changes, component_files)

            if scoped_changes.is_empty():
                continue

            # Build a scoped manifest view containing only this component's files
            scoped_manifest = AnalysisManifest(
                repo_state_hash=self.manifest.repo_state_hash,
                base_commit=self.manifest.base_commit,
                file_to_component={f: component_name for f in component_files},
                expanded_components=[component_name],
            )

            component_impacts[component_name] = analyze_impact(
                scoped_changes,
                scoped_manifest,
                self.static_analysis,
            )

        return component_impacts

    def _run_scoped_component_impacts(self, components: set[str]) -> None:
        """Run impact analysis inside each component scope and log summaries.

        This does not change the main update plan but enables recursive usage of
        the same impact logic for sub-analyses when their files change.
        """

        if not components or not self.component_impacts:
            return

        for component in sorted(components):
            impact = self.component_impacts.get(component)
            if not impact:
                continue

            logger.info(
                "[Scoped Impact] Component '%s' -> action=%s dirty=%s added=%s deleted=%s",
                component,
                impact.action.value,
                len(impact.dirty_components),
                len(impact.added_files),
                len(impact.deleted_files),
            )

            # If scoped impact wants component updates, trigger deeper handling.
            if impact.action in {UpdateAction.UPDATE_COMPONENTS, UpdateAction.PATCH_PATHS}:
                self._handle_scoped_component_update(component, impact)

    def _handle_scoped_component_update(self, component_name: str, impact: ChangeImpact) -> None:
        """Apply scoped impact decisions recursively for expanded components.

        - If only renames -> patch paths in sub-analysis and manifest slice.
        - If updates required -> run DetailsAgent on that component's sub-analysis
          scope and patch/save results.
        """

        assert self.analysis and self.manifest

        # Ensure this component is expanded (has a sub-analysis file)
        from output_generators.markdown import sanitize

        safe_name = sanitize(component_name)
        sub_path = self.output_dir / f"{safe_name}.json"
        if not sub_path.exists():
            return

        # Load sub-analysis
        sub_analysis = load_sub_analysis(self.output_dir, component_name)
        if not sub_analysis:
            return

        # Apply path patches for renames/deletions at this scope
        changed = patch_sub_analysis(sub_analysis, impact.deleted_files, impact.renames)

        # If action is only PATCH_PATHS, persist and exit
        if impact.action == UpdateAction.PATCH_PATHS:
            if changed:
                save_sub_analysis(sub_analysis, self.output_dir, component_name, self.manifest.expanded_components)
            return

        # For UPDATE_COMPONENTS, re-run DetailsAgent scoped to this component
        if impact.action == UpdateAction.UPDATE_COMPONENTS:
            # Build a scoped manifest for this component's files
            component_files = set(self.manifest.get_files_for_component(component_name))
            scoped_manifest = AnalysisManifest(
                repo_state_hash=self.manifest.repo_state_hash,
                base_commit=self.manifest.base_commit,
                file_to_component={f: component_name for f in component_files},
                expanded_components=[component_name],
            )

            # Detect additional changes inside the component scope
            scoped_changes = _filter_changes_for_scope(self.changes or ChangeSet(), component_files)
            scoped_impact = analyze_impact(scoped_changes, scoped_manifest, self.static_analysis)

            # If nothing to do, just persist patches
            if scoped_impact.action == UpdateAction.PATCH_PATHS and changed:
                save_sub_analysis(sub_analysis, self.output_dir, component_name, self.manifest.expanded_components)
                return

            if scoped_impact.action == UpdateAction.NONE:
                if changed:
                    save_sub_analysis(sub_analysis, self.output_dir, component_name, self.manifest.expanded_components)
                return

            # Re-run DetailsAgent on this component using the existing static analysis
            if not self.static_analysis:
                logger.info("No static analysis available for scoped re-expansion; skipping.")
                return

            from agents.details_agent import DetailsAgent
            from agents.meta_agent import MetaAgent
            from agents.planner_agent import plan_analysis
            from diagram_analysis.analysis_json import from_analysis_to_json

            meta_agent = MetaAgent(
                repo_dir=self.repo_dir,
                project_name=self.repo_dir.name,
                static_analysis=self.static_analysis,
            )
            meta_context = cast(MetaAnalysisInsights, meta_agent.analyze_project_metadata())

            details_agent = DetailsAgent(
                repo_dir=self.repo_dir,
                project_name=self.repo_dir.name,
                static_analysis=self.static_analysis,
                meta_context=meta_context,
            )

            # Find the component object in the main analysis to preserve metadata
            component_obj = next((c for c in self.analysis.components if c.name == component_name), None)
            if not component_obj:
                return

            subgraph_analysis, subgraph_clusters = details_agent.run(component_obj)

            # Save refreshed sub-analysis
            save_sub_analysis(subgraph_analysis, self.output_dir, component_name, self.manifest.expanded_components)

            # Update manifest slice with any new file assignments from the sub-analysis
            new_files: set[str] = set()
            for sub_comp in subgraph_analysis.components:
                for f in sub_comp.assigned_files:
                    new_files.add(f)
                    self.manifest.add_file(f, component_name)

            # Ensure parent analysis assigned_files reflect any new files
            for comp in self.analysis.components:
                if comp.name == component_name:
                    for f in new_files:
                        if f not in comp.assigned_files:
                            comp.assigned_files.append(f)

            # Save updated root analysis and manifest
            save_analysis(self.analysis, self.output_dir, self.manifest.expanded_components)
            save_manifest(self.manifest, self.output_dir)

    def _remove_deleted_files(self, deleted_files: list[str]) -> None:
        """Remove deleted files from analysis and manifest."""
        assert self.analysis and self.manifest

        for file_path in deleted_files:
            # Remove from manifest
            component_name = self.manifest.remove_file(file_path)

            if component_name:
                # Remove from component's assigned_files
                for component in self.analysis.components:
                    if component.name == component_name:
                        component.assigned_files = [f for f in component.assigned_files if f != file_path]
                        # Also remove from key_entities if referenced
                        component.key_entities = [e for e in component.key_entities if e.reference_file != file_path]
                        break

                logger.info(f"Removed deleted file '{file_path}' from component '{component_name}'")

    def _classify_new_files_in_component(self, component_name: str, new_files: list[str]) -> bool:
        """
        Run targeted file classification for new files within a component's sub-analysis.

        This loads the existing sub-analysis, classifies the new files into sub-components,
        and saves the updated analysis. Much more efficient than full re-expansion.

        Args:
            component_name: Name of the component to classify files for
            new_files: List of new file paths that need classification

        Returns:
            True if classification was successful, False otherwise
        """
        assert self.analysis and self.manifest and self.static_analysis

        # Find the component in the main analysis
        component = next(
            (c for c in self.analysis.components if c.name == component_name),
            None,
        )
        if not component:
            logger.warning(f"Component '{component_name}' not found for new file classification")
            return False

        # Load existing sub-analysis
        sub_analysis = load_sub_analysis(self.output_dir, component_name)
        if not sub_analysis:
            logger.warning(f"No sub-analysis found for component '{component_name}', cannot classify new files")
            return False

        logger.info(f"Running targeted file classification for {len(new_files)} new files in '{component_name}'")

        # Create subgraph cluster results for this component
        # This mirrors what DetailsAgent.run() does in step 1
        cluster_results = self._create_component_cluster_results(component)

        if not cluster_results:
            logger.warning(f"Could not create cluster results for '{component_name}', skipping targeted classification")
            return False

        # Import the classify_files functionality
        from agents.agent import LargeModelAgent
        from agents.meta_agent import MetaAgent

        # Create a minimal agent instance to access classify_files
        # We need the mixin methods to perform classification
        meta_agent = MetaAgent(
            self.repo_dir,
            self.static_analysis,
            self.repo_dir.name,
        )
        meta_context = meta_agent.analyze_project_metadata()

        agent = LargeModelAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.static_analysis,
            system_message="Classification agent for incremental updates",
        )

        try:
            # Add new files to the sub-analysis as unassigned (they'll be classified)
            # First, we need to ensure the new files are in the component's scope
            component_files = set(component.assigned_files)
            files_to_classify = [
                f for f in new_files if f in component_files or any(f.endswith(cf) for cf in component_files)
            ]

            if not files_to_classify:
                logger.info(f"No new files to classify for '{component_name}' (files may not be in component scope)")
                return True

            # Perform classification using the agent's classify_files method
            # This mimics DetailsAgent.run() step 5 but scoped to only new files
            agent.classify_files(sub_analysis, cluster_results, scope_files=files_to_classify)

            # Save the updated sub-analysis
            save_sub_analysis(sub_analysis, self.output_dir, component_name, self.manifest.expanded_components)

            logger.info(f"Successfully classified {len(files_to_classify)} new files in '{component_name}'")
            return True

        except Exception as e:
            logger.error(f"Failed to classify new files in '{component_name}': {e}")
            return False

    def _create_component_cluster_results(self, component) -> dict:
        """
        Create cluster results for a component's assigned files.

        This is a simplified version of _create_strict_component_subgraph from ClusterMethodsMixin
        that returns only the cluster_results dict without the string representation.

        Args:
            component: Component with assigned_files

        Returns:
            Dict mapping language -> ClusterResult for the subgraph
        """
        if not component.assigned_files:
            return {}

        # Convert assigned files to absolute paths for comparison
        assigned_file_set = set()
        for f in component.assigned_files:
            abs_path = os.path.join(self.repo_dir, f) if not os.path.isabs(f) else f
            assigned_file_set.add(abs_path)

        cluster_results: dict[str, ClusterResult] = {}

        if self.static_analysis is None:
            return cluster_results

        for lang in self.static_analysis.get_languages():
            cfg = self.static_analysis.get_cfg(lang)

            # Use strict filtering logic
            sub_cfg = cfg.filter_by_files(assigned_file_set)

            if sub_cfg.nodes:
                # Calculate clusters for the subgraph
                sub_cluster_result = sub_cfg.cluster()
                cluster_results[lang] = sub_cluster_result

        return cluster_results

    def _get_new_files_for_component(self, component_name: str, added_files: list[str]) -> list[str]:
        """
        Get the list of new files that belong to a specific component.

        This checks which of the added files were assigned to the given component
        by looking at the component's current assigned_files.

        Args:
            component_name: Name of the component
            added_files: List of all added files from the impact

        Returns:
            List of new file paths that belong to this component
        """
        assert self.analysis

        # Find the component
        component = next(
            (c for c in self.analysis.components if c.name == component_name),
            None,
        )
        if not component:
            return []

        # Get the component's current assigned files
        component_files = set(component.assigned_files)

        # Filter added files to those in this component
        new_files = []
        for file_path in added_files:
            if file_path in component_files:
                new_files.append(file_path)
            else:
                # Check if file_path matches any component file (handling relative vs absolute paths)
                for cf in component_files:
                    if file_path.endswith(cf) or cf.endswith(file_path):
                        new_files.append(file_path)
                        break

        return new_files
