"""
Impact analysis for incremental updates.

This module analyzes the impact of code changes and determines
the appropriate update action based on the magnitude of changes.
"""

import logging
from pathlib import Path

from diagram_analysis.incremental.models import (
    ChangeImpact,
    UpdateAction,
    STRUCTURAL_CHANGE_THRESHOLD,
    MAX_DIRTY_COMPONENTS_FOR_INCREMENTAL,
)
from diagram_analysis.manifest import AnalysisManifest
from repo_utils.change_detector import ChangeSet, ChangeType
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import CallGraph

logger = logging.getLogger(__name__)


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

    scoped_changes: list = []

    for change in changes.changes:
        if change.change_type == ChangeType.RENAMED:
            old_path = change.old_path or ""
            if in_scope(change.file_path) or in_scope(old_path):
                scoped_changes.append(change)
        else:
            if in_scope(change.file_path):
                scoped_changes.append(change)

    return ChangeSet(scoped_changes)
