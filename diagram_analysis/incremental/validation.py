"""Validation utilities for incremental analysis updates."""

import logging

from agents.agent_responses import AnalysisInsights
from agents.validation import (
    ValidationContext,
    validate_component_relationships,
    validate_key_entities,
    validate_qualified_names,
)
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import build_all_cluster_results

logger = logging.getLogger(__name__)


def validate_incremental_update(analysis: AnalysisInsights, static_analysis: StaticAnalysisResults) -> bool:
    """Validate the updated analysis after incremental changes.

    Runs validation checks to ensure the incremental update maintained
    consistency in component relationships and key entities.
    """
    logger.info("Running incremental update validation...")

    cluster_results = build_all_cluster_results(static_analysis)

    context = ValidationContext(
        cluster_results=cluster_results,
        cfg_graphs={lang: static_analysis.get_cfg(lang) for lang in static_analysis.get_languages()},
        static_analysis=static_analysis,
    )

    validators = [validate_component_relationships, validate_key_entities, validate_qualified_names]
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
        logger.warning("[Incremental Validation] Some validation checks failed")

    return all_valid
