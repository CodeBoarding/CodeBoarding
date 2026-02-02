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
        # (either from deleted files or from added files)
        #
        # An expanded component is one that has a sub-analysis JSON file.
        # We check file existence for backward compatibility with manifests
        # that have incorrect expanded_components data.
        components_to_reexpand: set[str] = set()

        # Add components with structural changes (from deleted files)
        for component_name in self.impact.components_needing_reexpansion:
            if self._is_expanded_component(component_name):
                components_to_reexpand.add(component_name)

        # Add components that received new files
        for component_name in components_with_new_files:
            if self._is_expanded_component(component_name):
                components_to_reexpand.add(component_name)

        # Step 5: Re-run DetailsAgent for components that need re-expansion
        reexpanded_components: list[str] = []
        if components_to_reexpand:
            reexpanded_components = self._reexpand_components(components_to_reexpand)

        # Step 6: Patch remaining dirty components (ones that don't need full re-expansion)
        patched_components: list[str] = []
        components_to_patch = self.impact.dirty_components - components_to_reexpand

        deleted_files = self.impact.deleted_files
        renames = self.impact.renames

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

        # Step 7: Update manifest with new commit
        from repo_utils.change_detector import get_current_commit
        from repo_utils import get_repo_state_hash

        new_commit = get_current_commit(self.repo_dir) or self.manifest.base_commit
        self.manifest.base_commit = new_commit
        self.manifest.repo_state_hash = get_repo_state_hash(self.repo_dir)

        # Step 8: Save updated files
        save_analysis(self.analysis, self.output_dir, self.manifest.expanded_components)
        save_manifest(self.manifest, self.output_dir)

        logger.info(
            f"Component update complete. " f"Re-expanded: {reexpanded_components}, Patched: {patched_components}"
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

    def _reexpand_components(self, component_names: set[str]) -> list[str]:
        """
        Re-run DetailsAgent for components that need sub-analysis regeneration.

        This is called when files are added/deleted from an expanded component,
        requiring the sub-analysis to be regenerated.

        Returns list of successfully re-expanded component names.
        """
        assert self.analysis and self.manifest

        if not component_names:
            return []

        logger.info(f"Re-expanding {len(component_names)} components: {component_names}")

        # Load static analysis (uses cache if available)
        from static_analyzer import get_static_analysis
        from agents.details_agent import DetailsAgent
        from agents.meta_agent import MetaAgent
        from agents.planner_agent import plan_analysis
        from diagram_analysis.analysis_json import from_analysis_to_json
        from output_generators.markdown import sanitize

        static_analysis = get_static_analysis(self.repo_dir)

        # Initialize agents
        meta_agent = MetaAgent(
            repo_dir=self.repo_dir,
            project_name=self.repo_dir.name,
            static_analysis=static_analysis,
        )
        meta_context = meta_agent.analyze_project_metadata()

        details_agent = DetailsAgent(
            repo_dir=self.repo_dir,
            project_name=self.repo_dir.name,
            static_analysis=static_analysis,
            meta_context=meta_context,
        )

        reexpanded: list[str] = []

        for component_name in component_names:
            # Find the component in analysis
            component = next(
                (c for c in self.analysis.components if c.name == component_name),
                None,
            )
            if not component:
                logger.warning(f"Component '{component_name}' not found for re-expansion")
                continue

            try:
                logger.info(f"Re-expanding component: {component_name}")

                # Run DetailsAgent to regenerate sub-analysis
                sub_analysis, _ = details_agent.run(component)

                # Get expandable sub-components
                new_components = plan_analysis(sub_analysis, parent_had_clusters=bool(component.source_cluster_ids))

                # Save sub-analysis
                safe_name = sanitize(component_name)
                output_path = self.output_dir / f"{safe_name}.json"
                with open(output_path, "w") as f:
                    f.write(from_analysis_to_json(sub_analysis, new_components))

                logger.info(f"Re-expanded component '{component_name}' -> {output_path}")
                reexpanded.append(component_name)

            except Exception as e:
                logger.error(f"Failed to re-expand component '{component_name}': {e}")

        return reexpanded

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
