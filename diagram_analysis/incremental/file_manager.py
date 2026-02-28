"""File management utilities for incremental updates."""

import logging
import os
from pathlib import Path

from agents.agent_responses import AnalysisInsights, FileMethodGroup
from diagram_analysis.incremental.io_utils import (
    load_sub_analysis,
    save_sub_analysis,
)
from diagram_analysis.manifest import AnalysisManifest
from repo_utils.ignore import should_skip_file
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import ClusterResult

logger = logging.getLogger(__name__)


def assign_new_files(
    new_files: list[str],
    analysis: AnalysisInsights,
    manifest: AnalysisManifest,
) -> set[str]:
    """Assign new files to components based on directory heuristics."""
    assigned_count = 0
    skipped_count = 0
    components_with_new_files: set[str] = set()

    for file_path in new_files:
        # Skip non-source files
        if should_skip_file(file_path):
            logger.debug(f"Skipping non-source file: {file_path}")
            skipped_count += 1
            continue

        # Try to find a component whose files share the same directory
        file_dir = str(Path(file_path).parent)

        best_component = None
        best_match_count = 0

        for component in analysis.components:
            # Count files in the same directory
            match_count = sum(1 for fg in component.file_methods if str(Path(fg.file_path).parent) == file_dir)
            if match_count > best_match_count:
                best_match_count = match_count
                best_component = component

        if best_component:
            best_component.file_methods.append(FileMethodGroup(file_path=file_path))
            manifest.add_file(file_path, best_component.component_id)
            assigned_count += 1
            components_with_new_files.add(best_component.component_id)
            logger.debug(f"Assigned new file '{file_path}' to component '{best_component.name}'")
        else:
            logger.debug(f"Could not assign new file '{file_path}' to any component")

    logger.info(f"File assignment: {assigned_count} assigned, {skipped_count} skipped (non-source)")
    return components_with_new_files


def remove_deleted_files(
    deleted_files: list[str],
    analysis: AnalysisInsights,
    manifest: AnalysisManifest,
) -> None:
    """Remove deleted files from analysis and manifest."""
    for file_path in deleted_files:
        # Remove from manifest (returns component_id)
        component_id = manifest.remove_file(file_path)

        if component_id:
            # Remove from component's file_methods
            for component in analysis.components:
                if component.component_id == component_id:
                    component.file_methods = [fg for fg in component.file_methods if fg.file_path != file_path]
                    # Also remove from key_entities if referenced
                    component.key_entities = [e for e in component.key_entities if e.reference_file != file_path]
                    break

            logger.info(f"Removed deleted file '{file_path}' from component '{component_id}'")


def classify_new_files_in_component(
    component_id: str,
    new_files: list[str],
    analysis: AnalysisInsights,
    manifest: AnalysisManifest,
    output_dir: Path,
    static_analysis: StaticAnalysisResults,
    repo_dir: Path,
) -> bool:
    """Run targeted file classification for new files within a component's sub-analysis.

    Loads existing sub-analysis, classifies new files, and saves results.
    Much more efficient than full re-expansion.
    """
    # Find the component in the main analysis
    component = next(
        (c for c in analysis.components if c.component_id == component_id),
        None,
    )
    if not component:
        logger.warning(f"Component '{component_id}' not found for new file classification")
        return False

    # Load existing sub-analysis
    sub_analysis = load_sub_analysis(output_dir, component_id)
    if not sub_analysis:
        logger.warning(f"No sub-analysis found for component '{component_id}', cannot classify new files")
        return False

    logger.info(f"Running targeted file classification for {len(new_files)} new files in '{component_id}'")

    # Create subgraph cluster results for this component
    # This mirrors what DetailsAgent.run() does in step 1
    cluster_results = create_component_cluster_results(component, static_analysis, repo_dir)

    if not cluster_results:
        logger.warning(f"Could not create cluster results for '{component_id}', skipping targeted classification")
        return False

    try:
        from agents.cluster_methods_mixin import ClusterMethodsMixin

        # Use a lightweight mixin instance to populate file_methods deterministically
        mixin = ClusterMethodsMixin.__new__(ClusterMethodsMixin)
        mixin.repo_dir = repo_dir
        mixin.static_analysis = static_analysis
        mixin.populate_file_methods(sub_analysis, cluster_results)

        # Save the updated sub-analysis
        save_sub_analysis(sub_analysis, output_dir, component_id, manifest.expanded_components)
        return True

    except Exception as e:
        logger.error(f"Failed to classify new files in '{component_id}': {e}")
        return False


def create_component_cluster_results(
    component,
    static_analysis: StaticAnalysisResults,
    repo_dir: Path,
) -> dict:
    """Create cluster results for a component's file_methods."""
    component_files = [fg.file_path for fg in component.file_methods]
    if not component_files:
        return {}

    # Convert files to absolute paths for comparison
    assigned_file_set = set()
    for f in component_files:
        abs_path = os.path.join(repo_dir, f) if not os.path.isabs(f) else f
        assigned_file_set.add(abs_path)

    cluster_results: dict[str, ClusterResult] = {}

    if static_analysis is None:
        return cluster_results

    for lang in static_analysis.get_languages():
        cfg = static_analysis.get_cfg(lang)

        # Use strict filtering logic
        sub_cfg = cfg.filter_by_files(assigned_file_set)

        if sub_cfg.nodes:
            # Calculate clusters for the subgraph
            sub_cluster_result = sub_cfg.cluster()
            cluster_results[lang] = sub_cluster_result

    return cluster_results


def get_new_files_for_component(
    component_id: str,
    added_files: list[str],
    analysis: AnalysisInsights,
) -> list[str]:
    """Get new files that belong to a specific component."""
    # Find the component
    component = next(
        (c for c in analysis.components if c.component_id == component_id),
        None,
    )
    if not component:
        return []

    component_files = {fg.file_path for fg in component.file_methods}

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
