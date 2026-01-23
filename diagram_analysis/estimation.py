import logging
import math
from pathlib import Path

# Use a module-level logger
logger = logging.getLogger(__name__)

def estimate_pipeline_time(source: Path, depth_level: int) -> None:
    """
    Calculate and log the estimated pipeline time based on lines of code (LOC)
    per language, using a log-based model.

    Args:
        source: Either a dictionary of LOC by language or a Path to the repository.
        depth_level: The analysis depth level.
    """
    # Import inside the function to avoid circular/heavy imports at module level
    from static_analyzer.scanner import ProjectScanner
    
    scanner = ProjectScanner(source)
    loc_by_language = {pl.language: pl.size for pl in scanner.scan()}

    total_loc = sum(loc_by_language.values())
    if total_loc <= 0:
        return

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

    # Formula: time = slope * log10(LOC) + intercept, where slope = 14.8590, intercept = -43.1970
    # See branch: estimate-running-times for how interpolation was derived.
    slope, intercept = 14.8590, -43.1970
    base_time_minutes = slope * math.log10(total_loc) + intercept

    # Adjust base time based on depth_level
    # Current estimates (from interpolation) are for level 2
    # Level 1: 0.5x, Level 2: 1.0x, Level 3: 2.0x
    depth_multiplier = 1.0
    if depth_level == 1:
        depth_multiplier = 0.5
    elif depth_level == 3:
        depth_multiplier = 2.0

    estimated_time_minutes = max(0, base_time_minutes) * effective_multiplier * depth_multiplier

    logger.info(
        f"Estimated pipeline time: {estimated_time_minutes:.1f} minutes "
        f"(based on {total_loc:,} LOC, depth level: {depth_level}, effective multiplier: {effective_multiplier:.2f})"
    )
