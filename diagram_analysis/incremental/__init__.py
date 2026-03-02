"""
Incremental analysis module for fast iterative updates.

This package provides functionality to:
- Analyze the impact of code changes
- Determine minimal update strategies
- Execute incremental updates without full reanalysis
- Patch file paths for renamed files

Usage:
    from diagram_analysis.incremental import IncrementalUpdater, analyze_impact

    updater = IncrementalUpdater(repo_dir, output_dir, run_id="example-run-id")
    if updater.can_run_incremental():
        impact = updater.analyze()
        success = updater.execute()
"""

from diagram_analysis.incremental.impact_analyzer import analyze_impact
from diagram_analysis.incremental.io_utils import (
    load_full_analysis,
    load_root_analysis,
    load_sub_analysis,
    save_analysis,
    save_sub_analysis,
)
from diagram_analysis.incremental.models import (
    MAX_DIRTY_COMPONENTS_FOR_INCREMENTAL,
    STRUCTURAL_CHANGE_THRESHOLD,
    ChangeImpact,
    UpdateAction,
)
from diagram_analysis.incremental.path_patching import (
    patch_paths_in_analysis,
    patch_paths_in_manifest,
    patch_sub_analysis,
)
from diagram_analysis.incremental.updater import IncrementalUpdater
from diagram_analysis.incremental.validation import validate_incremental_update

__all__ = [
    # Models
    "ChangeImpact",
    "UpdateAction",
    "STRUCTURAL_CHANGE_THRESHOLD",
    "MAX_DIRTY_COMPONENTS_FOR_INCREMENTAL",
    # Impact Analysis
    "analyze_impact",
    # Path Patching
    "patch_paths_in_analysis",
    "patch_paths_in_manifest",
    "patch_sub_analysis",
    # I/O Utilities
    "load_root_analysis",
    "load_full_analysis",
    "save_analysis",
    "load_sub_analysis",
    "save_sub_analysis",
    # Main Updater
    "IncrementalUpdater",
    # Validation
    "validate_incremental_update",
]
