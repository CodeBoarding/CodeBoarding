"""Incremental updater for executing update strategies."""

import logging
from pathlib import Path

from agents.agent_responses import AnalysisInsights
from diagram_analysis.incremental.path_patching import patch_sub_analysis
from diagram_analysis.incremental.io_utils import (
    load_analysis,
    load_sub_analysis,
    save_analysis,
    save_sub_analysis,
)
from diagram_analysis.incremental.models import ChangeImpact, UpdateAction
from diagram_analysis.incremental.path_patching import (
    patch_paths_in_analysis,
    patch_paths_in_manifest,
)
from diagram_analysis.incremental.impact_analyzer import analyze_impact
from diagram_analysis.incremental.component_checker import (
    is_expanded_component,
    component_has_only_renames,
    can_patch_sub_analysis,
)
from diagram_analysis.incremental.reexpansion import (
    ReexpansionContext,
    reexpand_components,
)
from diagram_analysis.incremental.file_manager import (
    assign_new_files,
    remove_deleted_files,
    classify_new_files_in_component,
    get_new_files_for_component,
)
from diagram_analysis.incremental.scoped_analysis import (
    analyze_expanded_component_impacts,
    run_scoped_component_impacts,
)
from diagram_analysis.incremental.validation import validate_incremental_update
from diagram_analysis.file_coverage import FileCoverage
from diagram_analysis.manifest import AnalysisManifest, load_manifest, save_manifest
from repo_utils import get_repo_state_hash
from repo_utils.change_detector import (
    ChangeSet,
    detect_changes_from_commit,
    get_current_commit,
)
from repo_utils.ignore import should_skip_file, RepoIgnoreManager
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import build_all_cluster_results

logger = logging.getLogger(__name__)


class IncrementalUpdater:
    """Executes incremental updates based on impact analysis."""

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
        """Execute the update based on impact analysis."""
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
        """Recompute affected components using updated cluster assignments."""
        if not self.impact or not self.manifest or not self.analysis:
            logger.warning("Cannot recompute dirty components: missing impact, manifest, or analysis")
            return

        logger.info("Recomputing dirty components with updated cluster assignments...")

        # Build cluster results from updated static analysis
        cluster_results = build_all_cluster_results(static_analysis)

        # Get the changed files
        changed_files: set[str] = set()
        changed_files.update(self.impact.renames.keys())
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
            if should_skip_file(file_path):
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
        """Find which component a file belongs to based on cluster membership."""
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
        new_commit = get_current_commit(self.repo_dir) or self.manifest.base_commit
        self.manifest.base_commit = new_commit
        self.manifest.repo_state_hash = get_repo_state_hash(self.repo_dir)

        # Save updated files
        save_analysis(self.analysis, self.output_dir, self.manifest.expanded_components)
        save_manifest(self.manifest, self.output_dir)

        logger.info("Path patching complete")
        return True

    def _execute_update_components(self) -> bool:
        """Execute targeted component updates.

        Updates assigned_files without LLM re-analysis where possible.
        Re-runs DetailsAgent only for expanded components with structural changes.
        """
        assert self.impact and self.manifest and self.analysis

        logger.info(f"Updating {len(self.impact.dirty_components)} components: {self.impact.dirty_components}")

        # Step 1: Handle deleted files first (before assigning new ones)
        if self.impact.deleted_files:
            remove_deleted_files(self.impact.deleted_files, self.analysis, self.manifest)

        # Step 2: Assign new files to components and track which ones got new files
        components_with_new_files: set[str] = set()
        if self.impact.added_files:
            components_with_new_files = assign_new_files(self.impact.added_files, self.analysis, self.manifest)

        # Step 3: Apply renames
        if self.impact.renames:
            patch_paths_in_analysis(self.analysis, self.impact.renames)
            patch_paths_in_manifest(self.manifest, self.impact.renames)

        # Step 4: Determine which expanded components need re-expansion
        components_to_reexpand: set[str] = set()
        components_to_patch: set[str] = set()
        components_to_classify: dict[str, list[str]] = {}

        for component_name in self.impact.components_needing_reexpansion | components_with_new_files:
            if not is_expanded_component(component_name, self.manifest, self.output_dir):
                continue

            if component_has_only_renames(component_name, self.manifest, self.impact):
                logger.info(f"Component '{component_name}' has only renames, will patch instead of re-expanding")
                components_to_patch.add(component_name)
            else:
                if can_patch_sub_analysis(
                    component_name,
                    self.manifest,
                    self.impact,
                    self.output_dir,
                    self.analysis,
                ):
                    logger.info(f"Component '{component_name}' can be patched without LLM re-analysis")
                    components_to_patch.add(component_name)
                    if component_name in components_with_new_files:
                        new_files = get_new_files_for_component(component_name, self.impact.added_files, self.analysis)
                        if new_files:
                            components_to_classify[component_name] = new_files
                else:
                    logger.info(f"Component '{component_name}' needs full re-expansion")
                    components_to_reexpand.add(component_name)

        # Step 5: Re-run DetailsAgent for components that need re-expansion
        reexpanded_components: list[str] = []
        if components_to_reexpand:
            context = ReexpansionContext(
                analysis=self.analysis,
                manifest=self.manifest,
                output_dir=self.output_dir,
                impact=self.impact,
                static_analysis=self.static_analysis,
            )
            reexpanded_components = reexpand_components(components_to_reexpand, self.repo_dir, context)

        # Step 5a: Sanity check
        for comp_name in reexpanded_components:
            if comp_name not in self.manifest.expanded_components:
                logger.warning(f"Component {comp_name} is not found in original analysis")

        # Step 5b: Run scoped impact summaries for changed expanded components
        run_scoped_component_impacts(
            components_to_reexpand | components_to_patch,
            self.component_impacts,
            self.changes or ChangeSet(),
            self.analysis,
            self.manifest,
            self.output_dir,
            self.static_analysis,
            self.repo_dir,
        )

        # Step 6: Patch components that don't need full re-expansion
        patched_components: list[str] = []
        classified_components: list[str] = []

        # First, run targeted classification for components with new files
        for component_name, new_files in components_to_classify.items():
            static_analysis = self.static_analysis
            if static_analysis is None:
                logger.debug(
                    "Skipping classification for %s because static_analysis is not available",
                    component_name,
                )
                continue

            if classify_new_files_in_component(
                component_name,
                new_files,
                self.analysis,
                self.manifest,
                self.output_dir,
                static_analysis,
                self.repo_dir,
            ):
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

            sub_analysis = load_sub_analysis(self.output_dir, component_name)
            if sub_analysis:
                if patch_sub_analysis(sub_analysis, self.impact.deleted_files, self.impact.renames):
                    save_sub_analysis(sub_analysis, self.output_dir, component_name)
                    logger.info(f"Component '{component_name}' sub-analysis patched")
                patched_components.append(component_name)
            else:
                logger.info(f"Component '{component_name}' has no sub-analysis, updating in place")
                patched_components.append(component_name)

        # Step 7: Validate the updated analysis
        if self.static_analysis:
            is_valid = validate_incremental_update(self.analysis, self.static_analysis)
            if not is_valid:
                logger.warning(
                    "Incremental update validation failed - analysis may have inconsistencies. "
                    "Consider re-running full analysis for complete results."
                )
        else:
            logger.warning("No static analysis available for validation")

        # Step 8: Update manifest with new commit
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

    def _analyze_expanded_component_impacts(self, changes: ChangeSet) -> dict[str, ChangeImpact]:
        """Run analyze_impact within each expanded component's scope."""
        if not self.manifest:
            return {}
        return analyze_expanded_component_impacts(changes, self.manifest, self.static_analysis)

    def update_file_coverage(
        self,
        current_analyzed_files: set[Path],
        all_text_files: set[Path],
    ) -> dict:
        """Update file coverage incrementally based on changes.

        Args:
            current_analyzed_files: Files analyzed in current incremental run
            all_text_files: All text files currently in repository

        Returns:
            Updated file coverage dictionary
        """
        ignore_manager = RepoIgnoreManager(self.repo_dir)
        coverage = FileCoverage(self.repo_dir, ignore_manager)

        if not self.changes:
            logger.warning("No changes detected, cannot update file coverage incrementally")
            # Build fresh coverage from current state
            return coverage.build(all_text_files, current_analyzed_files)

        # Load existing coverage
        existing_coverage = FileCoverage.load(self.output_dir)

        if existing_coverage is None:
            # No existing coverage, build fresh
            logger.info("No existing file coverage found, building fresh coverage")
            return coverage.build(all_text_files, current_analyzed_files)

        # Update incrementally
        return coverage.update(
            existing_coverage=existing_coverage,
            all_text_files=all_text_files,
            analyzed_files=current_analyzed_files,
            changes=self.changes,
        )
