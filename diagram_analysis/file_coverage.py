"""File coverage utility for tracking analyzed vs excluded files.

This module provides a unified interface for building and updating file coverage data,
used by both full analysis and incremental updates.
"""

import json
import logging
from collections import Counter
from collections.abc import Collection
from pathlib import Path
from typing import TYPE_CHECKING

from repo_utils import normalize_path, normalize_paths

if TYPE_CHECKING:
    from repo_utils.change_detector import ChangeSet
    from repo_utils.ignore import RepoIgnoreManager

logger = logging.getLogger(__name__)


class FileCoverage:
    """Manages file coverage data for repository analysis.

    Handles building fresh coverage for full analysis and updating existing coverage
    for incremental analysis. Uses Path objects and set operations for efficiency.
    """

    def __init__(self, repo_path: Path, ignore_manager: "RepoIgnoreManager"):
        """Initialize FileCoverage with repository path and ignore manager.

        Args:
            repo_path: Root path of the repository
            ignore_manager: Manager for determining file exclusion reasons
        """
        self.repo_path = Path(repo_path)
        self.ignore_manager = ignore_manager

    def build(self, all_text_files: Collection[Path], analyzed_files: Collection[Path]) -> dict:
        """Build file coverage data from scratch.

        Args:
            all_text_files: Set of all text file paths in the repository
            analyzed_files: Set of file paths that were analyzed

        Returns:
            Dictionary with analyzed_files, not_analyzed_files, and summary
        """
        # Normalize all paths to relative
        normalized_all = normalize_paths(all_text_files, self.repo_path)
        normalized_analyzed = normalize_paths(analyzed_files, self.repo_path)

        # Files not analyzed
        not_analyzed = normalized_all - normalized_analyzed

        # Build list of not analyzed files with reasons
        not_analyzed_files = []
        for file_path in sorted(not_analyzed):
            reason = self.ignore_manager.categorize_file(file_path)
            not_analyzed_files.append({"path": str(file_path), "reason": reason})

        # Count reasons using Counter
        reason_counts = Counter(f["reason"] for f in not_analyzed_files)

        return {
            "analyzed_files": sorted(str(f) for f in normalized_analyzed),
            "not_analyzed_files": not_analyzed_files,
            "summary": {
                "total_files": len(normalized_all),
                "analyzed": len(normalized_analyzed),
                "not_analyzed": len(not_analyzed),
                "not_analyzed_by_reason": dict(reason_counts),
            },
        }

    def update(
        self,
        existing_coverage: dict,
        all_text_files: Collection[Path],
        analyzed_files: Collection[Path],
        changes: "ChangeSet",
    ) -> dict:
        """Update existing file coverage incrementally based on changes.

        Merges existing coverage with current analysis results, applying
        repository changes (deletions, renames) to maintain consistency.

        Args:
            existing_coverage: Previous file coverage data
            all_text_files: Set of all text files currently in repository
            analyzed_files: Set of files analyzed in current run
            changes: Repository changes since last analysis

        Returns:
            Updated file coverage dictionary
        """
        # Normalize all paths
        normalized_all = normalize_paths(all_text_files, self.repo_path)
        normalized_analyzed = normalize_paths(analyzed_files, self.repo_path)

        # Start with existing analyzed files, then apply changes
        # Convert strings from JSON to Path objects
        existing_analyzed = {Path(p) for p in existing_coverage.get("analyzed_files", [])}

        # Apply changes to existing coverage
        existing_analyzed = self._apply_changes(existing_analyzed, changes, normalized_all)

        # Merge with current analysis results
        # Current analysis takes precedence for files it analyzed
        merged_analyzed = (existing_analyzed - normalized_analyzed) | normalized_analyzed

        # Recalculate not_analyzed based on current state
        not_analyzed = normalized_all - merged_analyzed

        # Build not_analyzed files list with reasons
        not_analyzed_files = []
        for file_path in sorted(not_analyzed):
            reason = self.ignore_manager.categorize_file(file_path)
            not_analyzed_files.append({"path": str(file_path), "reason": reason})

        reason_counts = Counter(f["reason"] for f in not_analyzed_files)

        return {
            "analyzed_files": sorted(str(f) for f in merged_analyzed),
            "not_analyzed_files": not_analyzed_files,
            "summary": {
                "total_files": len(normalized_all),
                "analyzed": len(merged_analyzed),
                "not_analyzed": len(not_analyzed),
                "not_analyzed_by_reason": dict(reason_counts),
            },
        }

    def _apply_changes(
        self,
        existing_analyzed: set[Path],
        changes: "ChangeSet",
        all_files: set[Path],
    ) -> set[Path]:
        """Apply repository changes to existing analyzed file set.

        Handles deleted files (remove from analyzed) and renamed files
        (update path in analyzed set).

        Args:
            existing_analyzed: Current set of analyzed files (normalized paths)
            changes: Repository changes
            all_files: Current set of all files in repository (normalized paths)

        Returns:
            Updated set of analyzed files after applying changes
        """
        result = existing_analyzed.copy()

        # Remove deleted files
        for deleted in changes.deleted_files:
            deleted_normalized = normalize_path(deleted, self.repo_path)
            result.discard(deleted_normalized)
            logger.debug(f"Removed deleted file from coverage: {deleted_normalized}")

        # Handle renames - update paths
        for old_path, new_path in changes.renames.items():
            old_normalized = normalize_path(old_path, self.repo_path)
            new_normalized = normalize_path(new_path, self.repo_path)
            if old_normalized in result:
                result.discard(old_normalized)
                # Only add new path if file still exists
                if new_normalized in all_files:
                    result.add(new_normalized)
                    logger.debug(f"Updated renamed file in coverage: {old_normalized} -> {new_normalized}")

        return result

    @staticmethod
    def load(output_dir: Path) -> dict | None:
        """Load existing file coverage data from output directory.

        Args:
            output_dir: Directory containing file_coverage.json

        Returns:
            File coverage dictionary or None if file doesn't exist or is invalid
        """
        coverage_path = output_dir / "file_coverage.json"
        if not coverage_path.exists():
            return None

        try:
            with open(coverage_path, "r") as f:
                data = json.load(f)
                # Validate basic structure
                if "analyzed_files" in data and "not_analyzed_files" in data:
                    return data
                logger.warning("Existing file_coverage.json has invalid structure")
                return None
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load existing file coverage: {e}")
            return None

    @staticmethod
    def save(output_dir: Path, coverage: dict) -> None:
        """Save file coverage data to output directory.

        Args:
            output_dir: Directory to save file_coverage.json
            coverage: File coverage dictionary to save
        """
        coverage_path = output_dir / "file_coverage.json"
        with open(coverage_path, "w") as f:
            json.dump(coverage, f, indent=2)
        logger.info(f"File coverage saved to {coverage_path}")
