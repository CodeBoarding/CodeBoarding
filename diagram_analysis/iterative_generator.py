"""Iterative diagram generator for incremental analysis.

This module provides the IterativeDiagramGenerator class which performs
incremental analysis based on git changes, avoiding full re-analysis
when only small portions of the codebase have changed.

The iterative analysis follows Approach 3: Two-Phase Quick Classification + Targeted Deep Analysis:
1. Phase 1 (Quick Triage): Classify changes as Cosmetic, Internal, or Structural
2. Phase 2 (Targeted Analysis): Execute appropriate actions based on classification
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from agents.abstraction_agent import AbstractionAgent
from agents.details_agent import DetailsAgent
from agents.meta_agent import MetaAgent
from agents.agent_responses import AnalysisInsights, Component
from diagram_analysis.analysis_json import from_analysis_to_json
from diagram_analysis.change_classifier import (
    ChangeClassifier,
    ChangeClassificationResult,
    ChangeType,
    ClassifiedChange,
)
from diagram_analysis.version import Version, IterativeAnalysisMetadata
from output_generators.markdown import sanitize
from repo_utils.file_hash import compute_file_hash, detect_moves
from repo_utils.git_diff import get_changed_files, get_current_commit, GitDiffResult
from static_analyzer import StaticAnalyzer

logger = logging.getLogger(__name__)


# Threshold for falling back to full analysis (percentage of files changed)
REWRITE_THRESHOLD = 0.5  # 50% of files changed triggers full re-analysis


class IterativeDiagramGenerator:
    """Performs incremental analysis based on git changes.

    This class orchestrates the iterative analysis workflow:
    1. Load previous analysis state
    2. Detect git changes since last analysis
    3. Classify changes (Phase 1 - Quick Triage)
    4. Execute targeted analysis (Phase 2)
    5. Merge results and update metadata
    """

    def __init__(
        self,
        repo_location: Path,
        output_dir: Path,
        repo_name: str,
        depth_level: int = 2,
        project_name: str | None = None,
    ):
        """Initialize the iterative generator.

        Args:
            repo_location: Path to the repository being analyzed
            output_dir: Directory containing previous analysis and where to save updates
            repo_name: Name of the repository
            depth_level: Maximum depth for component analysis
            project_name: Optional project name override
        """
        self.repo_location = repo_location
        self.output_dir = output_dir
        self.repo_name = repo_name
        self.depth_level = depth_level
        self.project_name = project_name or repo_name

        # Paths to key files
        self.analysis_path = output_dir / "analysis.json"
        self.version_path = output_dir / "codeboarding_version.json"
        self.metadata_path = output_dir / "iterative_metadata.json"

        # Will be initialized lazily
        self._static_analysis = None
        self._meta_context = None
        self._details_agent: DetailsAgent | None = None
        self._abstraction_agent: AbstractionAgent | None = None

    @property
    def static_analysis(self):
        """Lazily initialize static analysis."""
        if self._static_analysis is None:
            self._static_analysis = StaticAnalyzer(self.repo_location).analyze()
        return self._static_analysis

    def _get_meta_context(self):
        """Get or create meta context."""
        if self._meta_context is None:
            meta_agent = MetaAgent(
                repo_dir=self.repo_location,
                project_name=self.project_name,
                static_analysis=self.static_analysis,
            )
            self._meta_context = meta_agent.analyze_project_metadata()
        return self._meta_context

    def _get_details_agent(self) -> DetailsAgent:
        """Get or create details agent."""
        if self._details_agent is None:
            self._details_agent = DetailsAgent(
                repo_dir=self.repo_location,
                project_name=self.project_name,
                static_analysis=self.static_analysis,
                meta_context=self._get_meta_context(),
            )
        return self._details_agent

    def _get_abstraction_agent(self) -> AbstractionAgent:
        """Get or create abstraction agent."""
        if self._abstraction_agent is None:
            self._abstraction_agent = AbstractionAgent(
                repo_dir=self.repo_location,
                project_name=self.project_name,
                static_analysis=self.static_analysis,
                meta_context=self._get_meta_context(),
            )
        return self._abstraction_agent

    def analyze(self) -> tuple[list[str], dict[str, Any]]:
        """Perform iterative analysis if possible, fall back to full analysis otherwise.

        Returns:
            Tuple of (list of generated/updated file paths, stats dict)
        """
        start_time = time.time()
        stats: dict[str, Any] = {
            "mode": "unknown",
            "files_analyzed": 0,
            "components_updated": 0,
            "duration_seconds": 0,
        }

        # Step 1: Load previous state
        previous_state = self._load_previous_state()
        if previous_state is None:
            logger.info("No previous analysis found, falling back to full analysis")
            stats["mode"] = "full"
            stats["reason"] = "no_previous_analysis"
            # Return empty - caller should use DiagramGenerator for full analysis
            return [], stats

        previous_analysis, metadata = previous_state
        logger.info(f"Loaded previous analysis from commit {metadata.commit_hash}")

        # Step 2: Get current commit
        current_commit = get_current_commit(self.repo_location)
        if current_commit is None:
            logger.error("Could not determine current commit")
            stats["mode"] = "error"
            stats["reason"] = "no_current_commit"
            return [], stats

        if current_commit == metadata.commit_hash:
            logger.info("No changes detected since last analysis")
            stats["mode"] = "no_changes"
            stats["duration_seconds"] = time.time() - start_time
            return [], stats

        # Step 3: Detect changes via git diff
        git_diff = get_changed_files(self.repo_location, metadata.commit_hash, current_commit)
        if not git_diff.has_changes:
            logger.info("Git reports no file changes")
            stats["mode"] = "no_changes"
            stats["duration_seconds"] = time.time() - start_time
            return [], stats

        logger.info(
            f"Detected changes: {len(git_diff.added_files)} added, "
            f"{len(git_diff.modified_files)} modified, "
            f"{len(git_diff.deleted_files)} deleted, "
            f"{len(git_diff.renamed_files)} renamed"
        )

        # Step 4: Check for major refactoring
        total_files = len(metadata.file_content_hashes)
        if total_files > 0:
            change_ratio = git_diff.total_files_changed / total_files
            if change_ratio > REWRITE_THRESHOLD:
                logger.info(
                    f"Major refactoring detected ({change_ratio:.1%} files changed), " "falling back to full analysis"
                )
                stats["mode"] = "full"
                stats["reason"] = f"rewrite_threshold_exceeded ({change_ratio:.1%})"
                return [], stats

        # Step 5: Compute new hashes and detect moves
        new_hashes = self._compute_current_hashes()

        # Handle move detection for add/delete pairs
        moves, unmatched_deleted, unmatched_added = detect_moves(
            git_diff.deleted_files,
            git_diff.added_files,
            metadata.file_content_hashes,
            new_hashes,
        )

        # Merge git-detected renames with our move detection
        all_renamed = list(git_diff.renamed_files) + moves
        final_added = [f for f in unmatched_added if f not in [r[1] for r in all_renamed]]
        final_deleted = [f for f in unmatched_deleted if f not in [r[0] for r in all_renamed]]

        logger.info(
            f"After move detection: {len(final_added)} added, {len(final_deleted)} deleted, {len(all_renamed)} renamed"
        )

        # Step 6: Classify changes (Phase 1 - Quick Triage)
        classifier = ChangeClassifier(
            repo_dir=self.repo_location,
            previous_analysis=previous_analysis,
            old_hashes=metadata.file_content_hashes,
        )

        classification = classifier.classify_changes(
            added_files=final_added,
            modified_files=git_diff.modified_files,
            deleted_files=final_deleted,
            renamed_files=all_renamed,
            old_commit=metadata.commit_hash,
            new_hashes=new_hashes,
        )

        logger.info(f"Classification summary: {classification.summary()}")
        stats["classification"] = {
            "cosmetic": len(classification.cosmetic_changes),
            "internal": len(classification.internal_changes),
            "structural": len(classification.structural_changes),
            "new_files": len(classification.new_files),
            "deleted": len(classification.deleted_files),
            "moved": len(classification.moved_files),
        }

        # Step 7: Execute targeted analysis (Phase 2)
        updated_files, updated_analysis = self._execute_targeted_analysis(classification, previous_analysis, metadata)

        stats["mode"] = "incremental"
        stats["files_analyzed"] = len(updated_files)
        stats["components_updated"] = len(
            classification.components_needing_full_reanalysis | classification.components_needing_description_update
        )

        # Step 8: Save updated analysis and metadata
        self._save_analysis(updated_analysis, updated_files)
        self._save_metadata(current_commit, new_hashes)

        stats["duration_seconds"] = time.time() - start_time
        logger.info(
            f"Iterative analysis complete in {stats['duration_seconds']:.1f}s: "
            f"{stats['files_analyzed']} files updated, {stats['components_updated']} components affected"
        )

        return updated_files, stats

    def _load_previous_state(self) -> tuple[AnalysisInsights, IterativeAnalysisMetadata] | None:
        """Load previous analysis and metadata.

        Returns:
            Tuple of (AnalysisInsights, IterativeAnalysisMetadata) or None if not available
        """
        # Check if analysis file exists
        if not self.analysis_path.exists():
            logger.debug(f"Analysis file not found: {self.analysis_path}")
            return None

        # Load analysis
        try:
            with open(self.analysis_path) as f:
                analysis_data = json.load(f)
            previous_analysis = AnalysisInsights.model_validate(analysis_data)
        except Exception as e:
            logger.warning(f"Failed to load previous analysis: {e}")
            return None

        # Load metadata (or construct from version file if metadata doesn't exist)
        metadata: IterativeAnalysisMetadata | None = None
        if self.metadata_path.exists():
            try:
                with open(self.metadata_path) as f:
                    metadata_data = json.load(f)
                metadata = IterativeAnalysisMetadata.model_validate(metadata_data)
            except Exception as e:
                logger.warning(f"Failed to load metadata: {e}")
                metadata = self._construct_metadata_from_version()
        else:
            # Try to construct from version file
            metadata = self._construct_metadata_from_version()

        if metadata is None:
            return None

        return previous_analysis, metadata

    def _construct_metadata_from_version(self) -> IterativeAnalysisMetadata | None:
        """Construct metadata from version file (for backwards compatibility)."""
        if not self.version_path.exists():
            return None

        try:
            with open(self.version_path) as f:
                version_data = json.load(f)
            version = Version.model_validate(version_data)

            # We don't have file hashes, so this will be a partial metadata
            # The classifier will treat all files as modified
            return IterativeAnalysisMetadata(
                commit_hash=version.commit_hash,
                file_content_hashes={},  # Empty - will cause all files to be re-analyzed
            )
        except Exception as e:
            logger.warning(f"Failed to construct metadata from version: {e}")
            return None

    def _compute_current_hashes(self) -> dict[str, str]:
        """Compute content hashes for all source files in the repo."""
        hashes = {}
        for lang in self.static_analysis.get_languages():
            for file_path in self.static_analysis.get_source_files(lang):
                # Convert to relative path
                try:
                    rel_path = str(Path(file_path).relative_to(self.repo_location))
                except ValueError:
                    rel_path = file_path

                file_hash = compute_file_hash(Path(file_path))
                if file_hash:
                    hashes[rel_path] = file_hash

        return hashes

    def _execute_targeted_analysis(
        self,
        classification: ChangeClassificationResult,
        previous_analysis: AnalysisInsights,
        metadata: IterativeAnalysisMetadata,
    ) -> tuple[list[str], AnalysisInsights]:
        """Execute targeted analysis based on change classification.

        This is Phase 2 of the iterative analysis:
        - Cosmetic: Just log, no action
        - Internal: Lightweight description update
        - Structural: Full re-analysis of affected components
        - New files: Classify into components
        - Deleted: Remove from components

        Args:
            classification: The change classification result
            previous_analysis: Previous AnalysisInsights
            metadata: Previous metadata

        Returns:
            Tuple of (list of updated file paths, updated AnalysisInsights)
        """
        updated_files: list[str] = []

        # Start with a copy of the previous analysis
        updated_analysis = self._copy_analysis(previous_analysis)

        # Handle cosmetic changes - just log
        if classification.cosmetic_changes:
            logger.info(f"Skipping {len(classification.cosmetic_changes)} cosmetic changes")

        # Handle internal changes - lightweight description update
        if classification.internal_changes:
            self._handle_internal_changes(
                classification.internal_changes,
                updated_analysis,
                updated_files,
            )

        # Handle structural changes - full re-analysis of affected components
        if classification.structural_changes:
            self._handle_structural_changes(
                classification.structural_changes,
                updated_analysis,
                updated_files,
            )

        # Handle new files - classify into components
        if classification.new_files:
            self._handle_new_files(
                classification.new_files,
                updated_analysis,
                updated_files,
            )

        # Handle deleted files - remove from components
        if classification.deleted_files:
            self._handle_deleted_files(
                classification.deleted_files,
                updated_analysis,
            )

        # Handle moved files
        if classification.moved_files:
            self._handle_moved_files(
                classification.moved_files,
                updated_analysis,
            )

        return updated_files, updated_analysis

    def _copy_analysis(self, analysis: AnalysisInsights) -> AnalysisInsights:
        """Create a deep copy of the analysis."""
        return AnalysisInsights.model_validate(analysis.model_dump())

    def _handle_internal_changes(
        self,
        changes: list[ClassifiedChange],
        analysis: AnalysisInsights,
        updated_files: list[str],
    ) -> None:
        """Handle internal (implementation-only) changes."""
        logger.info(f"Handling {len(changes)} internal changes")

        # Group by component
        component_changes: dict[str, list[ClassifiedChange]] = {}
        for change in changes:
            for comp_name in change.affected_components:
                if comp_name not in component_changes:
                    component_changes[comp_name] = []
                component_changes[comp_name].append(change)

        # Update each affected component
        details_agent = self._get_details_agent()
        for comp_name, comp_changes in component_changes.items():
            component = self._get_component_by_name(analysis, comp_name)
            if component is None:
                logger.warning(f"Component {comp_name} not found in analysis")
                continue

            # Collect symbol diffs for this component
            symbol_diffs = [c.symbol_diff for c in comp_changes if c.symbol_diff is not None]
            if symbol_diffs:
                updated_component = details_agent.update_description_only(component, symbol_diffs)
                self._replace_component(analysis, updated_component)

                # Save updated component file
                safe_name = sanitize(comp_name)
                output_path = os.path.join(self.output_dir, f"{safe_name}.json")
                updated_files.append(output_path)

    def _handle_structural_changes(
        self,
        changes: list[ClassifiedChange],
        analysis: AnalysisInsights,
        updated_files: list[str],
    ) -> None:
        """Handle structural (API-level) changes."""
        logger.info(f"Handling {len(changes)} structural changes")

        # Get unique affected components
        affected_components = set()
        for change in changes:
            affected_components.update(change.affected_components)

        # Re-analyze each affected component
        details_agent = self._get_details_agent()
        for comp_name in affected_components:
            component = self._get_component_by_name(analysis, comp_name)
            if component is None:
                logger.warning(f"Component {comp_name} not found for re-analysis")
                continue

            logger.info(f"Re-analyzing component: {comp_name}")
            try:
                new_analysis, _ = details_agent.run(component)

                # Update the component in our analysis
                # Find the matching component in the new analysis
                for new_comp in new_analysis.components:
                    if new_comp.name == comp_name:
                        self._replace_component(analysis, new_comp)
                        break

                # Save the component analysis
                safe_name = sanitize(comp_name)
                output_path = os.path.join(self.output_dir, f"{safe_name}.json")
                updated_files.append(output_path)

            except Exception as e:
                logger.error(f"Failed to re-analyze component {comp_name}: {e}")

    def _handle_new_files(
        self,
        changes: list[ClassifiedChange],
        analysis: AnalysisInsights,
        updated_files: list[str],
    ) -> None:
        """Handle new files by classifying them into components."""
        logger.info(f"Handling {len(changes)} new files")

        new_file_paths = [c.file_path for c in changes]
        abstraction_agent = self._get_abstraction_agent()

        # Classify new files
        assignments = abstraction_agent.classify_new_files(new_file_paths, analysis)

        # Update component assignments
        for comp_name, files in assignments.items():
            if comp_name == "__NEW_COMPONENTS__":
                # Handle new component creation
                for name, desc, comp_files in files:  # type: ignore
                    new_component = Component(
                        name=name,
                        description=desc,
                        key_entities=[],
                        assigned_files=comp_files,
                        source_cluster_ids=[],
                    )
                    analysis.components.append(new_component)
                    logger.info(f"Created new component: {name} with {len(comp_files)} files")
            else:
                component = self._get_component_by_name(analysis, comp_name)
                if component:
                    component.assigned_files.extend(files)
                    logger.info(f"Added {len(files)} files to component: {comp_name}")

    def _handle_deleted_files(
        self,
        changes: list[ClassifiedChange],
        analysis: AnalysisInsights,
    ) -> None:
        """Handle deleted files by removing them from components."""
        logger.info(f"Handling {len(changes)} deleted files")

        for change in changes:
            for comp_name in change.affected_components:
                component = self._get_component_by_name(analysis, comp_name)
                if component and change.file_path in component.assigned_files:
                    component.assigned_files.remove(change.file_path)
                    logger.debug(f"Removed {change.file_path} from {comp_name}")

        # Check for empty components
        empty_components = [c for c in analysis.components if not c.assigned_files]
        if empty_components:
            logger.warning(
                f"Found {len(empty_components)} empty components after deletion: "
                f"{[c.name for c in empty_components]}"
            )
            # Don't automatically remove - let user decide

    def _handle_moved_files(
        self,
        changes: list[ClassifiedChange],
        analysis: AnalysisInsights,
    ) -> None:
        """Handle moved/renamed files."""
        logger.info(f"Handling {len(changes)} moved files")

        for change in changes:
            if not change.old_path:
                continue

            # Remove from old location
            for component in analysis.components:
                if change.old_path in component.assigned_files:
                    component.assigned_files.remove(change.old_path)
                    component.assigned_files.append(change.file_path)
                    logger.debug(f"Updated file path in {component.name}: {change.old_path} -> {change.file_path}")
                    break

    def _get_component_by_name(self, analysis: AnalysisInsights, name: str) -> Component | None:
        """Find a component by name."""
        for component in analysis.components:
            if component.name == name:
                return component
        return None

    def _replace_component(self, analysis: AnalysisInsights, new_component: Component) -> None:
        """Replace a component in the analysis."""
        for i, component in enumerate(analysis.components):
            if component.name == new_component.name:
                analysis.components[i] = new_component
                return
        # If not found, add it
        analysis.components.append(new_component)

    def _save_analysis(self, analysis: AnalysisInsights, updated_files: list[str]) -> None:
        """Save the updated analysis."""
        # Save main analysis file
        analysis_json = from_analysis_to_json(analysis, [])
        with open(self.analysis_path, "w") as f:
            f.write(analysis_json)
        logger.info(f"Saved updated analysis to {self.analysis_path}")

    def _save_metadata(self, commit_hash: str, file_hashes: dict[str, str]) -> None:
        """Save iterative analysis metadata."""
        metadata = IterativeAnalysisMetadata(
            commit_hash=commit_hash,
            analysis_timestamp=datetime.now(),
            file_content_hashes=file_hashes,
        )

        with open(self.metadata_path, "w") as f:
            f.write(metadata.model_dump_json(indent=2))
        logger.info(f"Saved metadata to {self.metadata_path}")

        # Also update version file
        version = Version(commit_hash=commit_hash, code_boarding_version="0.2.0")
        with open(self.version_path, "w") as f:
            f.write(version.model_dump_json(indent=2))


def run_iterative_analysis(
    repo_location: Path,
    output_dir: Path,
    repo_name: str,
    depth_level: int = 2,
    project_name: str | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Convenience function to run iterative analysis.

    Args:
        repo_location: Path to the repository
        output_dir: Directory for analysis output
        repo_name: Name of the repository
        depth_level: Maximum depth for component analysis
        project_name: Optional project name override

    Returns:
        Tuple of (list of updated file paths, stats dict)
    """
    generator = IterativeDiagramGenerator(
        repo_location=repo_location,
        output_dir=output_dir,
        repo_name=repo_name,
        depth_level=depth_level,
        project_name=project_name,
    )
    return generator.analyze()
