"""
Path patching utilities for incremental analysis.

This module provides functions to patch file paths in analysis objects
when files are renamed, without requiring LLM re-analysis.
"""

import logging
from pathlib import Path

from agents.agent_responses import AnalysisInsights
from diagram_analysis.manifest import AnalysisManifest

logger = logging.getLogger(__name__)


def patch_paths_in_analysis(
    analysis: AnalysisInsights,
    renames: dict[str, str],
) -> AnalysisInsights:
    """
    Patch file paths in analysis for renames (no LLM needed).

    Updates:
    - file_methods in each Component
    - reference_file in key_entities

    Args:
        analysis: The analysis to patch
        renames: Mapping of old_path -> new_path

    Returns:
        Patched analysis (modified in place, also returned)
    """
    if not renames:
        return analysis

    logger.info(f"Patching {len(renames)} renamed paths in analysis")

    for component in analysis.components:
        # Patch file_methods file paths
        for fg in component.file_methods:
            if fg.file_path in renames:
                fg.file_path = renames[fg.file_path]

        # Patch key_entities reference_file
        for entity in component.key_entities:
            if entity.reference_file and entity.reference_file in renames:
                old_path = entity.reference_file
                entity.reference_file = renames[old_path]
                logger.debug(f"Patched key_entity path: {old_path} -> {entity.reference_file}")

    return analysis


def patch_paths_in_manifest(
    manifest: AnalysisManifest,
    renames: dict[str, str],
) -> AnalysisManifest:
    """
    Patch file paths in manifest for renames.

    Args:
        manifest: The manifest to patch
        renames: Mapping of old_path -> new_path

    Returns:
        Patched manifest (modified in place, also returned)
    """
    for old_path, new_path in renames.items():
        manifest.update_file_path(old_path, new_path)

    return manifest


def patch_sub_analysis(
    sub_analysis: AnalysisInsights,
    deleted_files: list[str],
    renames: dict[str, str],
) -> bool:
    """
    Patch a sub-analysis by removing deleted files and applying renames.

    Returns True if any changes were made.
    """
    changed = False

    # Build a set of deleted file patterns (handle both with and without repo prefix)
    deleted_patterns: set[str] = set()
    for f in deleted_files:
        deleted_patterns.add(f)
        # Also add normalized versions
        if f.startswith("repos/"):
            # Strip "repos/RepoName/" prefix
            parts = f.split("/", 2)
            if len(parts) > 2:
                deleted_patterns.add(parts[2])
        else:
            deleted_patterns.add(f.lstrip("./"))

    # Build rename patterns (handle both with and without repo prefix)
    rename_map: dict[str, str] = {}
    for old, new in renames.items():
        rename_map[old] = new
        rename_map[old.lstrip("./")] = new
        if old.startswith("repos/"):
            parts = old.split("/", 2)
            if len(parts) > 2:
                rename_map[parts[2]] = new

    def file_is_deleted(path: str) -> bool:
        normalized = path.lstrip("./")
        if normalized in deleted_patterns or path in deleted_patterns:
            return True
        # Check if it ends with any deleted file
        for pattern in deleted_patterns:
            if path.endswith(pattern) or normalized.endswith(pattern):
                return True
        return False

    def get_renamed_path(path: str) -> str | None:
        normalized = path.lstrip("./")
        if normalized in rename_map:
            return rename_map[normalized]
        if path in rename_map:
            return rename_map[path]
        for old, new in rename_map.items():
            if path.endswith(old) or normalized.endswith(old):
                return new
        return None

    for component in sub_analysis.components:
        # Remove deleted files from file_methods
        orig_len = len(component.file_methods)
        component.file_methods = [fg for fg in component.file_methods if not file_is_deleted(fg.file_path)]
        if len(component.file_methods) < orig_len:
            changed = True

        # Apply renames to file_methods
        for fg in component.file_methods:
            new_path = get_renamed_path(fg.file_path)
            if new_path:
                fg.file_path = new_path
                changed = True

        # Remove key_entities referencing deleted files
        orig_entities = len(component.key_entities)
        component.key_entities = [
            e for e in component.key_entities if not (e.reference_file and file_is_deleted(e.reference_file))
        ]
        if len(component.key_entities) < orig_entities:
            changed = True

        # Apply renames to key_entities
        for entity in component.key_entities:
            if entity.reference_file:
                new_path = get_renamed_path(entity.reference_file)
                if new_path:
                    entity.reference_file = new_path
                    changed = True

    return changed
