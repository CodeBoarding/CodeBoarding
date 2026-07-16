"""File coverage utility for tracking analyzed vs excluded files."""

import json
import logging
from collections import Counter
from collections.abc import Collection
from pathlib import Path
from typing import TYPE_CHECKING

from repo_utils import normalize_paths

if TYPE_CHECKING:
    from repo_utils.ignore import RepoIgnoreManager

logger = logging.getLogger(__name__)


class FileCoverage:
    """Manages file coverage data for repository analysis."""

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
            with open(coverage_path, "r", encoding="utf-8") as f:
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
        with open(coverage_path, "w", encoding="utf-8") as f:
            json.dump(coverage, f, indent=2)
        logger.info(f"File coverage saved to {coverage_path}")
