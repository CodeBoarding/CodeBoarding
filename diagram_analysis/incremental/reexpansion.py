"""
Re-expansion utilities for incremental analysis.

This module provides functions for re-expanding components that need
sub-analysis regeneration during incremental updates.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from tqdm import tqdm

from agents.agent_responses import AnalysisInsights, MetaAnalysisInsights
from agents.details_agent import DetailsAgent
from agents.meta_agent import MetaAgent
from agents.planner_agent import plan_analysis
from diagram_analysis.analysis_json import from_analysis_to_json
from diagram_analysis.incremental.io_utils import load_sub_analysis, save_sub_analysis
from diagram_analysis.incremental.models import ChangeImpact
from diagram_analysis.incremental.path_patching import patch_sub_analysis
from diagram_analysis.manifest import AnalysisManifest
from output_generators.markdown import sanitize
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


@dataclass
class ReexpansionContext:
    """Context holding common parameters needed for re-expansion operations."""

    analysis: AnalysisInsights
    manifest: AnalysisManifest
    output_dir: Path
    impact: ChangeImpact | None = None
    static_analysis: StaticAnalysisResults | None = None


def _subcomponent_has_only_renames(
    component_name: str,
    sub_analysis: AnalysisInsights,
    impact: ChangeImpact,
) -> bool:
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
        impact: The change impact to analyze

    Returns:
        True if the sub-analysis changes are just renames that can be patched
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


def reexpand_single_component(
    component_name: str,
    details_agent: DetailsAgent,
    context: ReexpansionContext,
) -> str | None:
    """Process a single component for re-expansion.

    First checks if the existing sub-analysis can be patched instead of
    regenerated (if changes are just renames/reassigns within this component).

    Args:
        component_name: The name of the component to re-expand
        details_agent: The DetailsAgent instance to use for re-expansion
        context: ReexpansionContext containing analysis, output_dir, impact, etc.

    Returns:
        The component name if successful, None otherwise
    """
    # Find the component in analysis
    component = next(
        (c for c in context.analysis.components if c.name == component_name),
        None,
    )
    if not component:
        logger.warning(f"Component '{component_name}' not found for re-expansion")
        return None

    try:
        # Check if we can patch the existing sub-analysis instead of re-running
        safe_name = sanitize(component_name)
        sub_analysis_path = context.output_dir / f"{safe_name}.json"

        if sub_analysis_path.exists():
            existing_sub_analysis = load_sub_analysis(context.output_dir, component_name)
            if existing_sub_analysis and context.impact:
                # Check if changes within this component are just renames
                if _subcomponent_has_only_renames(
                    component_name,
                    existing_sub_analysis,
                    context.impact,
                ):
                    logger.info(
                        f"Component '{component_name}' sub-analysis has only renames, patching instead of re-expanding"
                    )

                    # Patch the sub-analysis
                    if patch_sub_analysis(
                        existing_sub_analysis,
                        context.impact.deleted_files if context.impact else [],
                        context.impact.renames if context.impact else {},
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


def reexpand_components(
    component_names: set[str],
    repo_dir: Path,
    context: ReexpansionContext,
) -> list[str]:
    """Re-run DetailsAgent for components that need sub-analysis regeneration.

    This is called when files are added/deleted from an expanded component,
    requiring the sub-analysis to be regenerated.

    Components are processed in parallel using ThreadPoolExecutor for efficiency.

    Args:
        component_names: Set of component names to re-expand
        repo_dir: Path to the repository root
        context: ReexpansionContext containing analysis, manifest, output_dir, static_analysis, etc.

    Returns:
        List of successfully re-expanded component names
    """
    if not component_names:
        return []

    logger.info(f"Re-expanding {len(component_names)} components: {component_names}")

    if not context.static_analysis:
        logger.error("No static analysis available for re-expansion")
        return []

    # Initialize agents using existing static analysis
    meta_agent = MetaAgent(
        repo_dir=repo_dir,
        project_name=repo_dir.name,
        static_analysis=context.static_analysis,
    )
    meta_context = cast(MetaAnalysisInsights, meta_agent.analyze_project_metadata())

    details_agent = DetailsAgent(
        repo_dir=repo_dir,
        project_name=repo_dir.name,
        static_analysis=context.static_analysis,
        meta_context=meta_context,
    )

    reexpanded: list[str] = []
    max_workers = min(os.cpu_count() or 4, 8)  # Limit to 8 workers max

    # Process components in parallel using ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all component processing tasks
        future_to_component = {
            executor.submit(
                reexpand_single_component,
                component_name,
                details_agent,
                context,
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
