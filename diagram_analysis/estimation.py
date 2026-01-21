import json
import logging
import math
import sys

logger = logging.getLogger(__name__)


def estimate_pipeline_time(loc_by_language: dict[str, int], estimate_only: bool = False) -> None:
    """
    Calculate and log the estimated pipeline time based on lines of code (LOC)
    per language, using a log-based model.
    """
    total_loc = sum(loc_by_language.values())
    if total_loc <= 0:
        return None

    # Multipliers relative to Python (1.0)
    # These reflect relative complexity/time for static analysis and agent exploring the codebase.
    multipliers = {
        "python": 1.0,
        "java": 2.0,
        "go": 1.0,
        "typescript": 1.0,
        "javascript": 1.0,
        "php": 1.0,
    }

    # Calculate weighted multiplier based on LOC distribution
    weighted_sum = 0.0
    for lang, loc in loc_by_language.items():
        multiplier = multipliers.get(lang.lower(), 1.0)
        weighted_sum += loc * multiplier

    effective_multiplier = weighted_sum / total_loc

    # Formula: time = a * log10(LOC) + b, where a = 14.8590, b = -43.1970
    # See branch: estimate-running-times for how interpolation was derived.
    a, b = 14.8590, -43.1970
    base_time_minutes = a * math.log10(total_loc) + b
    estimated_time_minutes = max(0, base_time_minutes) * effective_multiplier

    logger.info(
        f"Estimated pipeline time: {estimated_time_minutes:.1f} minutes "
        f"(based on {total_loc:,} LOC, effective multiplier: {effective_multiplier:.2f})"
    )

    if estimate_only:
        sys.exit(0)
