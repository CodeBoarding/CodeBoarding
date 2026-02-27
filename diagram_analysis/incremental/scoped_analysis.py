"""
Scoped analysis functions for incremental diagram updates.

This module provides standalone functions for analyzing impacts within
component scopes and handling scoped component updates.
"""

import logging
from pathlib import Path

from agents.llm_config import initialize_llms

from agents.agent_responses import AnalysisInsights, FileMethodGroup, MetaAnalysisInsights
from agents.details_agent import DetailsAgent
from agents.meta_agent import MetaAgent
from diagram_analysis.incremental.io_utils import (
    load_sub_analysis,
    save_analysis,
    save_sub_analysis,
)
from diagram_analysis.incremental.models import ChangeImpact, UpdateAction
from diagram_analysis.incremental.path_patching import patch_sub_analysis
from diagram_analysis.incremental.impact_analyzer import (
    analyze_impact,
    _filter_changes_for_scope,
)
from diagram_analysis.manifest import AnalysisManifest, save_manifest as save_manifest_func
from repo_utils.change_detector import ChangeSet
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


def analyze_expanded_component_impacts(
    changes: ChangeSet,
    manifest: AnalysisManifest,
    static_analysis: StaticAnalysisResults | None,
) -> dict[str, ChangeImpact]:
    """Run impact analysis within each expanded component's scope.

    Filters changes to component files and analyzes each expanded component
    independently using the same impact logic.
    """

    if not manifest:
        return {}

    component_impacts: dict[str, ChangeImpact] = {}

    for component_id in manifest.expanded_components:
        # Collect the files currently assigned to this component
        component_files = {f for f, comp in manifest.file_to_component.items() if comp == component_id}

        if not component_files:
            continue

        scoped_changes = _filter_changes_for_scope(changes, component_files)

        if scoped_changes.is_empty():
            continue

        # Build a scoped manifest view containing only this component's files
        scoped_manifest = AnalysisManifest(
            repo_state_hash=manifest.repo_state_hash,
            base_commit=manifest.base_commit,
            file_to_component={f: component_id for f in component_files},
            expanded_components=[component_id],
        )

        component_impacts[component_id] = analyze_impact(
            scoped_changes,
            scoped_manifest,
            static_analysis,
        )

    return component_impacts


def handle_scoped_component_update(
    component_id: str,
    impact: ChangeImpact,
    changes: ChangeSet,
    analysis: AnalysisInsights,
    manifest: AnalysisManifest,
    output_dir: Path,
    static_analysis: StaticAnalysisResults | None,
    repo_dir: Path,
) -> None:
    """Apply scoped impact decisions for expanded components.

    Patches paths for renames or re-runs DetailsAgent for component updates.
    """
    sub_analysis = load_sub_analysis(output_dir, component_id)
    if not sub_analysis:
        return

    # Apply path patches for renames/deletions at this scope
    changed = patch_sub_analysis(sub_analysis, impact.deleted_files, impact.renames)

    # If action is only PATCH_PATHS, persist and exit
    if impact.action == UpdateAction.PATCH_PATHS:
        if changed:
            save_sub_analysis(sub_analysis, output_dir, component_id, manifest.expanded_components)
        return

    # For UPDATE_COMPONENTS, re-run DetailsAgent scoped to this component
    if impact.action == UpdateAction.UPDATE_COMPONENTS:
        # Build a scoped manifest for this component's files
        component_files = set(manifest.get_files_for_component(component_id))
        scoped_manifest = AnalysisManifest(
            repo_state_hash=manifest.repo_state_hash,
            base_commit=manifest.base_commit,
            file_to_component={f: component_id for f in component_files},
            expanded_components=[component_id],
        )

        # Detect additional changes inside the component scope
        scoped_changes = _filter_changes_for_scope(changes or ChangeSet(), component_files)
        scoped_impact = analyze_impact(scoped_changes, scoped_manifest, static_analysis)

        # If nothing to do, just persist patches
        if scoped_impact.action == UpdateAction.PATCH_PATHS and changed:
            save_sub_analysis(sub_analysis, output_dir, component_id, manifest.expanded_components)
            return

        if scoped_impact.action == UpdateAction.NONE:
            if changed:
                save_sub_analysis(sub_analysis, output_dir, component_id, manifest.expanded_components)
            return

        # Re-run DetailsAgent on this component using the existing static analysis
        if not static_analysis:
            logger.info("No static analysis available for scoped re-expansion; skipping.")
            return

        agent_llm, parsing_llm = initialize_llms()

        meta_agent = MetaAgent(
            repo_dir=repo_dir,
            project_name=repo_dir.name,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
        )
        meta_context = meta_agent.get_meta_context()

        details_agent = DetailsAgent(
            repo_dir=repo_dir,
            project_name=repo_dir.name,
            static_analysis=static_analysis,
            meta_context=meta_context,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
        )

        # Find the component object in the main analysis to preserve metadata
        component_obj = next((c for c in analysis.components if c.component_id == component_id), None)
        if not component_obj:
            return

        subgraph_analysis, subgraph_clusters = details_agent.run(component_obj)

        # Save refreshed sub-analysis
        save_sub_analysis(subgraph_analysis, output_dir, component_id, manifest.expanded_components)

        # Update manifest slice with any new file assignments from the sub-analysis
        new_files: set[str] = set()
        for sub_comp in subgraph_analysis.components:
            for fg in sub_comp.file_methods:
                new_files.add(fg.file_path)
                manifest.add_file(fg.file_path, component_id)

        # Ensure parent analysis file_methods reflect any new files
        for comp in analysis.components:
            if comp.component_id == component_id:
                existing_files = {fg.file_path for fg in comp.file_methods}
                for f in new_files:
                    if f not in existing_files:
                        comp.file_methods.append(FileMethodGroup(file_path=f))

        # Save updated root analysis and manifest
        save_analysis(analysis, output_dir, manifest.expanded_components)
        save_manifest_func(manifest, output_dir)


def run_scoped_component_impacts(
    components: set[str],
    component_impacts: dict[str, ChangeImpact],
    changes: ChangeSet,
    analysis: AnalysisInsights,
    manifest: AnalysisManifest,
    output_dir: Path,
    static_analysis: StaticAnalysisResults | None,
    repo_dir: Path,
) -> None:
    """Run impact analysis inside each component scope and log summaries.

    Applies scoped updates to components with UPDATE_COMPONENTS or PATCH_PATHS actions.
    """

    if not components or not component_impacts:
        return

    for component in sorted(components):
        impact = component_impacts.get(component)
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
            handle_scoped_component_update(
                component_id=component,
                impact=impact,
                changes=changes,
                analysis=analysis,
                manifest=manifest,
                output_dir=output_dir,
                static_analysis=static_analysis,
                repo_dir=repo_dir,
            )
