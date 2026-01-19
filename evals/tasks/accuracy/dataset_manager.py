import json
import logging
import os
from pathlib import Path
from typing import Any

from evals.tasks.accuracy.models import CodeSizeCategory, DatasetEntry

logger = logging.getLogger(__name__)

# Hugging Face dataset configuration
HF_REPO_ID = "brovatten/codeboarding"
HF_REPO_TYPE = "dataset"


def _ensure_dataset_downloaded(local_path: Path, filename: str) -> bool:
    if local_path.exists():
        return False  # Already exists, no download needed

    try:
        from huggingface_hub import hf_hub_download

        logger.info("Downloading %s from Hugging Face Hub (%s)...", filename, HF_REPO_ID)

        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Download to the target directory
        hf_hub_download(
            repo_id=HF_REPO_ID,
            filename=filename,
            repo_type=HF_REPO_TYPE,
            local_dir=local_path.parent,
            token=os.getenv("HF_TOKEN"),  # Optional: for private datasets
        )

        logger.info("Successfully downloaded %s", filename)
        return True  # Downloaded

    except ImportError:
        raise RuntimeError(
            "huggingface_hub is required to download datasets. " "Install it with: pip install huggingface_hub"
        )
    except Exception as e:
        raise RuntimeError(f"Failed to download {filename} from Hugging Face Hub: {e}")


class DatasetManager:
    """
    Manages the ground-truth dataset for accuracy evaluation.

    Provides clean interfaces for loading and filtering dataset entries
    based on project, code size, and depth level criteria.

    Datasets are automatically downloaded from Hugging Face Hub if not
    present locally. Set HF_TOKEN environment variable for private datasets.

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
        self.project_root = project_root
        self._cache: list[DatasetEntry] | None = None

    @property
    def datasets_dir(self) -> Path:
        return self.project_root / "evals" / "tasks" / "accuracy" / "datasets"

    @property
    def dataset_path(self) -> Path:
        return self.datasets_dir / "train.json"

    @property
    def test_dataset_path(self) -> Path:
        return self.datasets_dir / "test.json"

    def _ensure_datasets(self) -> None:
        """Ensure required dataset files are downloaded."""
        _ensure_dataset_downloaded(self.dataset_path, "train.json")

    def ensure_datasets_downloaded(self) -> None:
        """
        Explicitly download all required datasets from Hugging Face Hub.

        Call this at the start of evaluation to ensure datasets are available
        before any processing begins. Downloads are skipped if files exist locally.
        """
        logger.info("Checking for required datasets...")

        datasets = [
            ("train.json", self.dataset_path),
            ("test.json", self.test_dataset_path),
        ]

        for filename, path in datasets:
            was_downloaded = _ensure_dataset_downloaded(path, filename)
            if was_downloaded:
                logger.info("Loaded %s (downloaded from Hugging Face Hub)", filename)
            else:
                logger.info("Loaded %s (cached locally)", filename)

        logger.info("All datasets ready.")

    def load_all(self) -> list[DatasetEntry]:
        if self._cache is not None:
            return self._cache

        self._ensure_datasets()

        if not self.dataset_path.exists():
            raise FileNotFoundError(f"Missing dataset file at {self.dataset_path}")

        raw_data = json.loads(self.dataset_path.read_text(encoding="utf-8"))

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
        entries = self.load_all()

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
        size_labels = {c.label for c in code_sizes}
        return [e for e in entries if e.code_size in size_labels]

    def _filter_by_depth(
        self,
        entries: list[DatasetEntry],
        depth_level: int,
    ) -> list[DatasetEntry]:
        return [e for e in entries if e.level_of_depth == depth_level]

    def _limit_per_size(
        self,
        entries: list[DatasetEntry],
        limit: int,
    ) -> list[DatasetEntry]:
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
        return entry.data

    def get_unique_graph_ids(self) -> set[str]:
        entries = self.load_all()
        return {e.graph_id for e in entries if e.graph_id}

    def get_available_depths(self, graph_id: str | None = None) -> set[int]:
        entries = self.load_all()
        if graph_id:
            entries = [e for e in entries if e.graph_id == graph_id]
        return {e.level_of_depth for e in entries}
