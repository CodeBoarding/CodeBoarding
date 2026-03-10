"""Re-expansion utilities for incremental analysis."""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from tqdm import tqdm

from agents.llm_config import initialize_llms
from agents.agent_responses import AnalysisInsights
from agents.details_agent import DetailsAgent
from agents.meta_agent import MetaAgent
from agents.planner_agent import get_expandable_components
from diagram_analysis.incremental.io_utils import load_sub_analysis, save_sub_analysis
from diagram_analysis.incremental.models import ChangeImpact
from diagram_analysis.incremental.path_patching import patch_sub_analysis
from diagram_analysis.incremental.component_checker import subcomponent_has_only_renames
from diagram_analysis.manifest import AnalysisManifest
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


@dataclass
class ReexpansionContext:
    """Context for re-expansion operations."""

    analysis: AnalysisInsights
    manifest: AnalysisManifest
    output_dir: Path
    impact: ChangeImpact | None = None
    static_analysis: StaticAnalysisResults | None = None


def reexpand_single_component(
    component_id: str,
    details_agent: DetailsAgent,
    context: ReexpansionContext,
) -> str | None:
    """Process a single component for re-expansion.

    Checks if existing sub-analysis can be patched instead of regenerated.
    """
    # Find the component in analysis
    component = next(
        (c for c in context.analysis.components if c.component_id == component_id),
        None,
    )
    if not component:
        logger.warning(f"Component '{component_id}' not found for re-expansion")
        return None

    try:
        # Check if we can patch the existing sub-analysis instead of re-running
        existing_sub_analysis = load_sub_analysis(context.output_dir, component_id)

        if existing_sub_analysis and context.impact:
            # Check if changes within this component are just renames
            if subcomponent_has_only_renames(
                component_id,
                existing_sub_analysis,
                context.impact,
            ):
                logger.info(
                    f"Component '{component_id}' sub-analysis has only renames, patching instead of re-expanding"
                )

                # Patch the sub-analysis
                if patch_sub_analysis(
                    existing_sub_analysis,
                    context.impact.deleted_files if context.impact else [],
                    context.impact.renames if context.impact else {},
                ):
                    save_sub_analysis(existing_sub_analysis, context.output_dir, component_id)
                    logger.info(f"Patched component '{component_id}' sub-analysis")
                    return component_id

        # If patching wasn't possible or changes are structural, re-run DetailsAgent
        logger.info(f"Re-expanding component: {component_id}")

        # Run DetailsAgent to regenerate sub-analysis
        sub_analysis, _ = details_agent.run(component)

        # Save sub-analysis into the unified file
        save_sub_analysis(sub_analysis, context.output_dir, component_id)

        logger.info(f"Re-expanded component '{component_id}'")
        return component_id

    except Exception as e:
        logger.error(f"Failed to re-expand component '{component_id}': {e}")
        return None


def reexpand_components(
    component_ids: set[str],
    repo_dir: Path,
    context: ReexpansionContext,
) -> list[str]:
    """Re-run DetailsAgent for components needing sub-analysis regeneration.

    Processes components in parallel for efficiency.
    """
    if not component_ids:
        return []

    logger.info(f"Re-expanding {len(component_ids)} components: {component_ids}")

    if not context.static_analysis:
        logger.error("No static analysis available for re-expansion")
        return []

    agent_llm, parsing_llm = initialize_llms()

    # Initialize agents using existing static analysis
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
        static_analysis=context.static_analysis,
        meta_context=meta_context,
        agent_llm=agent_llm,
        parsing_llm=parsing_llm,
    )

    reexpanded: list[str] = []
    max_workers = min(os.cpu_count() or 4, 8)  # Limit to 8 workers max

    # Process components in parallel using ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all component processing tasks
        future_to_component = {
            executor.submit(
                reexpand_single_component,
                component_id,
                details_agent,
                context,
            ): component_id
            for component_id in component_ids
        }

        # Collect results as they complete
        for future in tqdm(
            as_completed(future_to_component),
            total=len(future_to_component),
            desc="Re-expanding components",
        ):
            component_id = future_to_component[future]
            try:
                result = future.result()
                if result:
                    reexpanded.append(result)
            except Exception as exc:
                logger.error(f"Component {component_id} generated an exception: {exc}")

    logger.info(f"Successfully re-expanded {len(reexpanded)}/{len(component_ids)} components")
    return reexpanded
