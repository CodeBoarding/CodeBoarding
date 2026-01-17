"""
Dataset management for accuracy evaluation.

Handles loading, filtering, and querying of the ground-truth training dataset.
"""

import json
import logging
from pathlib import Path
from typing import Any

from evals.tasks.accuracy.models import CodeSizeCategory, DatasetEntry

logger = logging.getLogger(__name__)


class DatasetManager:
    """
    Manages the ground-truth dataset for accuracy evaluation.

    Provides clean interfaces for loading and filtering dataset entries
    based on project, code size, and depth level criteria.

    Example:
        manager = DatasetManager(project_root)
        entries = manager.get_entries(
            graph_id="markitdown",
            code_sizes=[CodeSizeCategory.SMALL, CodeSizeCategory.MEDIUM],
            depth_level=1,
            limit_per_size=5,
        )
    """

    def __init__(self, project_root: Path):
        """
        Initialize the dataset manager.

        Args:
            project_root: Root directory of the CodeBoarding project
        """
        self.project_root = project_root
        self._cache: list[DatasetEntry] | None = None

    @property
    def dataset_path(self) -> Path:
        """Path to the training dataset JSON file."""
        return self.project_root / "evals" / "tasks" / "accuracy" / "datasets" / "train.json"

    def load_all(self) -> list[DatasetEntry]:
        """
        Load all entries from the dataset.

        Returns:
            List of all dataset entries

        Raises:
            FileNotFoundError: If dataset file doesn't exist
        """
        if self._cache is not None:
            return self._cache

        if not self.dataset_path.exists():
            raise FileNotFoundError(f"Missing dataset file at {self.dataset_path}")

        raw_data = json.loads(self.dataset_path.read_text(encoding="utf-8"))

        # Handle both single entry and list formats
        if not isinstance(raw_data, list):
            raw_data = [raw_data]

        entries = [DatasetEntry.from_raw(entry) for entry in raw_data if isinstance(entry, dict)]

        self._cache = entries
        return entries

    def get_entries(
        self,
        *,
        graph_id: str | None = None,
        code_sizes: list[CodeSizeCategory] | None = None,
        depth_level: int | None = None,
        limit_per_size: int | None = None,
    ) -> list[DatasetEntry]:
        """
        Get filtered dataset entries.

        Args:
            graph_id: Filter by project/graph ID (e.g., "markitdown")
            code_sizes: Filter by code size categories
            depth_level: Filter by depth level (1, 2, etc.)
            limit_per_size: Maximum entries per code size bin

        Returns:
            Filtered list of dataset entries
        """
        entries = self.load_all()

        # Apply filters in sequence
        if graph_id is not None:
            entries = self._filter_by_graph_id(entries, graph_id)

        if code_sizes is not None:
            entries = self._filter_by_code_sizes(entries, code_sizes)

        if depth_level is not None:
            entries = self._filter_by_depth(entries, depth_level)

        if limit_per_size is not None and limit_per_size > 0:
            entries = self._limit_per_size(entries, limit_per_size)

        return entries

    def _filter_by_graph_id(
        self,
        entries: list[DatasetEntry],
        graph_id: str,
    ) -> list[DatasetEntry]:
        """Filter entries by graph ID."""
        matching = [e for e in entries if e.graph_id == graph_id]
        if not matching:
            logger.warning(
                "No dataset entries found for graph_id '%s'. "
                "Ensure the ground-truth dataset contains entries for this project.",
                graph_id,
            )
        return matching

    def _filter_by_code_sizes(
        self,
        entries: list[DatasetEntry],
        code_sizes: list[CodeSizeCategory],
    ) -> list[DatasetEntry]:
        """Filter entries by code size categories."""
        size_labels = {c.label for c in code_sizes}
        return [e for e in entries if e.code_size in size_labels]

    def _filter_by_depth(
        self,
        entries: list[DatasetEntry],
        depth_level: int,
    ) -> list[DatasetEntry]:
        """Filter entries by depth level."""
        return [e for e in entries if e.level_of_depth == depth_level]

    def _limit_per_size(
        self,
        entries: list[DatasetEntry],
        limit: int,
    ) -> list[DatasetEntry]:
        """Limit number of entries per code size bin."""
        per_size_counts: dict[str, int] = {}
        limited: list[DatasetEntry] = []

        for entry in entries:
            code_size = entry.code_size or "unknown"
            current_count = per_size_counts.get(code_size, 0)

            if current_count >= limit:
                continue

            per_size_counts[code_size] = current_count + 1
            limited.append(entry)

        return limited

    def get_raw_data(self, entry: DatasetEntry) -> dict[str, Any]:
        """
        Get the raw diagram data from an entry.

        Args:
            entry: The dataset entry

        Returns:
            The raw diagram JSON data
        """
        return entry.data

    def get_unique_graph_ids(self) -> set[str]:
        """Get all unique graph IDs in the dataset."""
        entries = self.load_all()
        return {e.graph_id for e in entries if e.graph_id}

    def get_available_depths(self, graph_id: str | None = None) -> set[int]:
        """Get all available depth levels, optionally filtered by graph ID."""
        entries = self.load_all()
        if graph_id:
            entries = [e for e in entries if e.graph_id == graph_id]
        return {e.level_of_depth for e in entries}
