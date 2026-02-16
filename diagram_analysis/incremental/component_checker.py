"""
Component checking utilities for incremental analysis.

This module provides standalone functions for checking component states
and determining if they can be patched or need re-expansion.
"""

import logging
from pathlib import Path

from agents.agent_responses import AnalysisInsights
from diagram_analysis.incremental.io_utils import load_sub_analysis
from diagram_analysis.incremental.models import ChangeImpact
from diagram_analysis.manifest import AnalysisManifest

logger = logging.getLogger(__name__)


def is_expanded_component(component_id: str, manifest: AnalysisManifest | None, output_dir: Path) -> bool:
    """Check if a component has a sub-analysis (is expanded).

    Checks the manifest first, then falls back to checking the unified analysis.json.
    """
    # Check manifest first
    if manifest and component_id in manifest.expanded_components:
        return True

    # Fallback: check if sub-analysis exists in the unified file
    sub = load_sub_analysis(output_dir, component_id)
    return sub is not None


def component_has_only_renames(
    component_id: str, manifest: AnalysisManifest | None, impact: ChangeImpact | None
) -> bool:
    """Check if a component's structural changes are just file renames.

    Returns True if all deleted files are old rename paths and all modified
    files are new rename paths (no true additions or deletions).
    """
    if not impact or not manifest:
        return False

    # Get all files associated with this component
    component_files = set()
    for file_path, comp in manifest.file_to_component.items():
        if comp == component_id:
            component_files.add(file_path)

    # Check if all "deleted" files are actually old paths of renames
    deleted_in_component = set()
    for file_path in impact.deleted_files:
        if file_path in component_files:
            deleted_in_component.add(file_path)

    # Check if all "modified" files are actually new paths of renamed files
    modified_in_component = set()
    for file_path in impact.modified_files:
        if file_path in component_files:
            modified_in_component.add(file_path)

    # Get renames that affect this component
    renames_in_component = {}
    for old_path, new_path in impact.renames.items():
        if old_path in component_files or new_path in component_files:
            renames_in_component[old_path] = new_path

    # Log detailed analysis for debugging
    logger.debug(
        f"Component '{component_id}' change analysis: "
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
        logger.debug(f"Component '{component_id}' has only renames")
    else:
        logger.debug(
            f"Component '{component_id}' has true structural changes: "
            f"deleted_are_renames={deleted_are_all_renames}, "
            f"modified_are_renames={modified_are_all_renames}"
        )

    # If both conditions are true, this component only has renames
    return deleted_are_all_renames and modified_are_all_renames


def can_patch_sub_analysis(
    component_id: str,
    manifest: AnalysisManifest | None,
    impact: ChangeImpact | None,
    output_dir: Path,
    analysis: AnalysisInsights | None,
) -> bool:
    """Check if a component's sub-analysis can be patched without LLM re-analysis.

    Can patch if changes are limited to file assignments without affecting
    the component's logical structure. Returns False for deletions or
    if sub-analysis doesn't exist.
    """
    if not analysis or not manifest:
        return False

    # Check component exists
    component = next(
        (c for c in analysis.components if c.component_id == component_id),
        None,
    )
    if not component:
        return False

    # Load existing sub-analysis from the unified file
    sub_analysis = load_sub_analysis(output_dir, component_id)
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
    for file_path in impact.added_files if impact else []:
        if file_path in subcomponent_files:
            has_additions = True
            break

    # Check for deleted files in sub-components
    for file_path in impact.deleted_files if impact else []:
        if file_path in subcomponent_files:
            has_deletions = True
            break

    # Check for renames in sub-components
    for old_path, new_path in impact.renames.items() if impact else {}:
        if old_path in subcomponent_files or new_path in subcomponent_files:
            has_renames = True
            break

    # We can patch if there are file changes but no structural logic changes
    # For additions, we'll handle them via targeted classification rather than full re-expansion
    logger.debug(
        f"Component '{component_id}' sub-analysis check: "
        f"additions={has_additions}, deletions={has_deletions}, renames={has_renames}"
    )

    # Can patch if:
    # - Only renames: just patch paths
    # - Additions: we'll run targeted classification (handled separately)
    # - Deletions: need to check if it affects structure
    if has_deletions:
        # Deletions might affect component structure, need re-expansion
        logger.info(f"Component '{component_id}' has deletions, needs re-expansion")
        return False

    return True


def subcomponent_has_only_renames(
    component_id: str, sub_analysis: AnalysisInsights, impact: ChangeImpact | None
) -> bool:
    """Check if changes within a component's sub-analysis are just renames.

    Similar to component_has_only_renames but operates at the sub-component level.
    Returns True if all structural changes in sub-components are just renames.
    """
    if not impact:
        return False

    # Collect all files from sub-components
    subcomponent_files: set[str] = set()
    for sub_component in sub_analysis.components:
        subcomponent_files.update(sub_component.assigned_files)

    # Check deleted files in sub-components
    deleted_in_subcomponent = set()
    for file_path in impact.deleted_files:
        if file_path in subcomponent_files:
            deleted_in_subcomponent.add(file_path)

    # Check modified files in sub-components
    modified_in_subcomponent = set()
    for file_path in impact.modified_files:
        if file_path in subcomponent_files:
            modified_in_subcomponent.add(file_path)

    # Get renames that affect sub-components
    renames_in_subcomponent = {}
    for old_path, new_path in impact.renames.items():
        if old_path in subcomponent_files or new_path in subcomponent_files:
            renames_in_subcomponent[old_path] = new_path

    # Log detailed analysis
    logger.debug(
        f"Sub-component analysis for '{component_id}': "
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
        logger.debug(f"Sub-analysis for '{component_id}' has only renames, can be patched")
    else:
        logger.debug(
            f"Sub-analysis for '{component_id}' has true structural changes: "
            f"deleted_are_renames={deleted_are_all_renames}, "
            f"modified_are_renames={modified_are_all_renames}"
        )

    return deleted_are_all_renames and modified_are_all_renames
